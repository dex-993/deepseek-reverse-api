"""账号池管理模块 - 管理多个DeepSeek账号

支持：
- 从JSON文件加载账号密码
- 自动登录获取Token
- Token轮询、健康检查、自动故障转移
"""

import asyncio
import random
import time
import json
import os
from typing import List, Dict, Optional, Any, Set
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum

from .client import DeepSeekClient
from .proxy_adapter import get_proxy_manager


class TokenStatus(Enum):
    """Token状态"""
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    RATE_LIMITED = "rate_limited"


@dataclass
class TokenInfo:
    """Token信息"""
    token: str
    status: TokenStatus = TokenStatus.UNKNOWN
    fail_count: int = 0
    success_count: int = 0
    last_used: Optional[str] = None
    last_checked: Optional[str] = None
    error_message: Optional[str] = None
    average_response_time: float = 0.0
    total_requests: int = 0
    added_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'token': self.token[:20] + '...' + self.token[-10:] if len(self.token) > 30 else self.token,
            'status': self.status.value,
            'fail_count': self.fail_count,
            'success_count': self.success_count,
            'last_used': self.last_used,
            'last_checked': self.last_checked,
            'error_message': self.error_message,
            'average_response_time': self.average_response_time,
            'total_requests': self.total_requests,
            'added_at': self.added_at,
        }
    
    def mark_success(self, response_time: float = 0):
        """标记成功"""
        self.success_count += 1
        self.fail_count = 0
        self.status = TokenStatus.HEALTHY
        self.error_message = None
        self.last_used = datetime.now().isoformat()
        self.total_requests += 1
        
        # 更新平均响应时间
        if self.average_response_time == 0:
            self.average_response_time = response_time
        else:
            self.average_response_time = (
                self.average_response_time * (self.total_requests - 1) + response_time
            ) / self.total_requests
    
    def mark_fail(self, error: str = ""):
        """标记失败"""
        self.fail_count += 1
        self.error_message = error
        self.last_used = datetime.now().isoformat()
        
        if self.fail_count >= 3:
            self.status = TokenStatus.UNHEALTHY
    
    def mark_rate_limited(self):
        """标记速率限制"""
        self.status = TokenStatus.RATE_LIMITED
        self.last_used = datetime.now().isoformat()


class AccountInfo:
    """账号信息"""
    
    def __init__(self, email: str, password: str, token: Optional[str] = None):
        self.email = email
        self.password = password
        self.token = token
        self.last_login = None
        self.login_error = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'email': self.email,
            'password': self.password,
            'token': self.token,
            'last_login': self.last_login,
            'login_error': self.login_error
        }


class AccountPool:
    """账号池管理器"""
    
    def __init__(self, storage_file: Optional[str] = None, accounts_file: Optional[str] = None):
        self.tokens: Dict[str, TokenInfo] = {}
        self.accounts: Dict[str, AccountInfo] = {}
        self._lock = asyncio.Lock()
        self._current_index = 0
        self.storage_file = storage_file or "account_pool.json"
        self.accounts_file = accounts_file or "accounts.json"
        self._initialized = False
    
    async def init(self):
        """初始化账号池"""
        if self._initialized:
            return
        
        # 从环境变量加载Token
        await self._load_from_env()
        
        # 从JSON文件加载账号
        await self._load_from_accounts_file()
        
        # 从存储文件加载状态
        await self._load_from_file()
        
        # 登录所有账号获取Token
        await self._login_all_accounts()
        
        self._initialized = True
        print(f"[AccountPool] Initialized with {len(self.tokens)} tokens, {len(self.accounts)} accounts")
    
    async def _load_from_env(self):
        """从环境变量加载Token"""
        tokens_str = os.environ.get('DEEPSEEK_TOKENS', '')
        if not tokens_str:
            return
        
        # 支持多种分隔符
        tokens = []
        for sep in ['\n', ',', ';']:
            if sep in tokens_str:
                tokens = [t.strip() for t in tokens_str.split(sep) if t.strip()]
                break
        
        if not tokens:
            tokens = [tokens_str.strip()]
        
        for token in tokens:
            if token not in self.tokens:
                self.tokens[token] = TokenInfo(token=token)
                print(f"[AccountPool] Added token from env: {token[:20]}...")
    
    async def _load_from_accounts_file(self):
        """从JSON文件加载账号"""
        if not os.path.exists(self.accounts_file):
            return
        
        try:
            with open(self.accounts_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 支持列表格式
            if isinstance(data, list):
                for account_data in data:
                    email = account_data.get('email')
                    password = account_data.get('password')
                    token = account_data.get('token')
                    
                    if email and password:
                        self.accounts[email] = AccountInfo(email, password, token)
                        if token:
                            if token not in self.tokens:
                                self.tokens[token] = TokenInfo(token=token)
                        print(f"[AccountPool] Added account: {email}")
            
            # 支持字典格式
            elif isinstance(data, dict):
                for email, account_data in data.items():
                    if isinstance(account_data, dict):
                        password = account_data.get('password')
                        token = account_data.get('token')
                        if password:
                            self.accounts[email] = AccountInfo(email, password, token)
                            if token:
                                if token not in self.tokens:
                                    self.tokens[token] = TokenInfo(token=token)
                            print(f"[AccountPool] Added account: {email}")
            
            print(f"[AccountPool] Loaded {len(self.accounts)} accounts from {self.accounts_file}")
        except Exception as e:
            print(f"[AccountPool] Failed to load accounts from file: {e}")
    
    async def _load_from_file(self):
        """从文件加载Token状态"""
        if not os.path.exists(self.storage_file):
            return
        
        try:
            with open(self.storage_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for token_data in data.get('tokens', []):
                token = token_data.get('token')
                if token and token in self.tokens:
                    # 恢复状态
                    info = self.tokens[token]
                    info.fail_count = token_data.get('fail_count', 0)
                    info.success_count = token_data.get('success_count', 0)
                    info.average_response_time = token_data.get('average_response_time', 0)
                    info.total_requests = token_data.get('total_requests', 0)
                    
                    # 恢复状态枚举
                    status_str = token_data.get('status', 'unknown')
                    try:
                        info.status = TokenStatus(status_str)
                    except ValueError:
                        info.status = TokenStatus.UNKNOWN
            
            print(f"[AccountPool] Loaded {len(data.get('tokens', []))} tokens from file")
        except Exception as e:
            print(f"[AccountPool] Failed to load from file: {e}")
    
    async def _login_all_accounts(self):
        """登录所有账号获取Token"""
        if not self.accounts:
            return
        
        print(f"[AccountPool] Logging in {len(self.accounts)} accounts...")
        
        for email, account in self.accounts.items():
            if not account.token or self._is_token_expired(account.token):
                try:
                    token = await self._login_account(email, account.password)
                    if token:
                        account.token = token
                        account.last_login = datetime.now().isoformat()
                        account.login_error = None
                        
                        if token not in self.tokens:
                            self.tokens[token] = TokenInfo(token=token)
                        print(f"[AccountPool] Login successful: {email}")
                except Exception as e:
                    account.login_error = str(e)
                    print(f"[AccountPool] Login failed for {email}: {e}")
    
    async def _login_account(self, email: str, password: str) -> Optional[str]:
        """登录单个账号获取Token"""
        import requests
        
        url = "https://chat.deepseek.com/api/v0/users/login"
        
        headers = {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,de-DE;q=0.5,de;q=0.4",
            "cache-control": "no-cache",
            "content-type": "application/json",
            "pragma": "no-cache",
            "priority": "u=1, i",
            "sec-ch-ua": '"Microsoft Edge";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-app-version": "20241129.1",
            "x-client-locale": "zh_CN",
            "x-client-platform": "web",
            "x-client-timezone-offset": "28800",
            "x-client-version": "1.8.0",
            "referrer": "https://chat.deepseek.com/sign_in"
        }
        
        payload = {
            "email": email,
            "mobile": "",
            "password": password,
            "area_code": "",
            "device_id": "",
            "os": "web"
        }
        
        # 尝试使用代理
        session = None
        try:
            proxy_manager = get_proxy_manager()
            session = proxy_manager.create_session(use_vless=True)
        except:
            session = requests.Session()
        
        response = session.post(
            url,
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code != 200:
            raise Exception(f"登录失败: HTTP {response.status_code}, {response.text}")
        
        data = response.json()
        
        if data.get("code") != 0:
            error_msg = data.get("msg") or data.get("data", {}).get("biz_msg") or "Unknown error"
            raise Exception(f"登录失败: {error_msg}")
        
        # 提取 Token
        biz_data = data.get("data", {}).get("biz_data", {})
        user = biz_data.get("user", {})
        token = user.get("token")
        
        if not token:
            raise Exception("登录成功但未获取到 Token")
        
        return token
    
    def _is_token_expired(self, token: str) -> bool:
        """检查Token是否过期（简单判断）"""
        # DeepSeek Token 通常较长，这里简单判断
        return len(token) < 50
    
    async def save(self):
        """保存Token状态到文件"""
        try:
            data = {
                'updated_at': datetime.now().isoformat(),
                'tokens': [token_info.to_dict() for token_info in self.tokens.values()],
                'accounts': [account.to_dict() for account in self.accounts.values()]
            }
            
            # 先写入临时文件
            temp_file = self.storage_file + '.tmp'
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            # 原子替换
            if os.path.exists(self.storage_file):
                os.replace(temp_file, self.storage_file)
            else:
                os.rename(temp_file, self.storage_file)
            
        except Exception as e:
            print(f"[AccountPool] Failed to save: {e}")
    
    def add_token(self, token: str) -> bool:
        """添加Token到池"""
        if token in self.tokens:
            return False
        
        self.tokens[token] = TokenInfo(token=token)
        print(f"[AccountPool] Added token: {token[:20]}...")
        return True
    
    def add_account(self, email: str, password: str) -> bool:
        """添加账号到池"""
        if email in self.accounts:
            return False
        
        self.accounts[email] = AccountInfo(email, password)
        print(f"[AccountPool] Added account: {email}")
        return True
    
    def remove_token(self, token: str) -> bool:
        """从池中移除Token"""
        if token not in self.tokens:
            return False
        
        del self.tokens[token]
        print(f"[AccountPool] Removed token: {token[:20]}...")
        return True
    
    def remove_account(self, email: str) -> bool:
        """从池中移除账号"""
        if email not in self.accounts:
            return False
        
        account = self.accounts[email]
        if account.token and account.token in self.tokens:
            del self.tokens[account.token]
        
        del self.accounts[email]
        print(f"[AccountPool] Removed account: {email}")
        return True
    
    async def get_healthy_token(self, strategy: str = 'round_robin') -> Optional[str]:
        """获取健康的Token
        
        Args:
            strategy: 选择策略 ('round_robin', 'random', 'least_used')
        
        Returns:
            Token字符串或None
        """
        async with self._lock:
            healthy_tokens = [
                t for t in self.tokens.values()
                if t.status in [TokenStatus.HEALTHY, TokenStatus.UNKNOWN]
            ]
            
            if not healthy_tokens:
                # 如果没有健康Token，尝试重新登录账号
                await self._login_all_accounts()
                healthy_tokens = [
                    t for t in self.tokens.values()
                    if t.status in [TokenStatus.HEALTHY, TokenStatus.UNKNOWN]
                ]
            
            if not healthy_tokens:
                # 如果仍然没有健康Token，尝试使用所有Token
                healthy_tokens = list(self.tokens.values())
            
            if not healthy_tokens:
                return None
            
            if strategy == 'random':
                selected = random.choice(healthy_tokens)
            elif strategy == 'least_used':
                selected = min(healthy_tokens, key=lambda t: t.total_requests)
            else:  # round_robin
                healthy_list = healthy_tokens
                selected = healthy_list[self._current_index % len(healthy_list)]
                self._current_index += 1
            
            return selected.token
    
    async def mark_token_result(self, token: str, success: bool, error: str = "", response_time: float = 0):
        """标记Token使用结果"""
        async with self._lock:
            if token not in self.tokens:
                return
            
            info = self.tokens[token]
            if success:
                info.mark_success(response_time)
            else:
                info.mark_fail(error)
            
            # 定期保存
            if info.total_requests % 10 == 0:
                await self.save()
    
    async def check_token_health(self, token: str) -> Dict[str, Any]:
        """检查单个Token的健康状态"""
        if token not in self.tokens:
            return {'valid': False, 'error': 'Token not found in pool'}
        
        info = self.tokens[token]
        start_time = time.time()
        
        try:
            client = DeepSeekClient(token=token, use_proxy=False)
            health = await client.check_token_health()
            
            response_time = time.time() - start_time
            
            if health.get('valid'):
                info.mark_success(response_time)
                info.last_checked = datetime.now().isoformat()
                return {
                    'valid': True,
                    'token': token[:20] + '...' + token[-10:] if len(token) > 30 else token,
                    'response_time': response_time,
                    'status': 'healthy'
                }
            else:
                info.mark_fail(health.get('error', 'Unknown error'))
                info.last_checked = datetime.now().isoformat()
                return {
                    'valid': False,
                    'token': token[:20] + '...' + token[-10:] if len(token) > 30 else token,
                    'error': health.get('error', 'Unknown error'),
                    'status': 'unhealthy'
                }
        
        except Exception as e:
            info.mark_fail(str(e))
            info.last_checked = datetime.now().isoformat()
            return {
                'valid': False,
                'token': token[:20] + '...' + token[-10:] if len(token) > 30 else token,
                'error': str(e),
                'status': 'unhealthy'
            }
    
    async def check_all_tokens_health(self) -> List[Dict[str, Any]]:
        """检查所有Token的健康状态"""
        results = []
        
        for token in list(self.tokens.keys()):
            result = await self.check_token_health(token)
            results.append(result)
        
        await self.save()
        return results
    
    def get_stats(self) -> Dict[str, Any]:
        """获取账号池统计信息"""
        total = len(self.tokens)
        healthy = sum(1 for t in self.tokens.values() if t.status == TokenStatus.HEALTHY)
        unhealthy = sum(1 for t in self.tokens.values() if t.status == TokenStatus.UNHEALTHY)
        unknown = sum(1 for t in self.tokens.values() if t.status == TokenStatus.UNKNOWN)
        rate_limited = sum(1 for t in self.tokens.values() if t.status == TokenStatus.RATE_LIMITED)
        
        return {
            'total_tokens': total,
            'healthy': healthy,
            'unhealthy': unhealthy,
            'unknown': unknown,
            'rate_limited': rate_limited,
            'total_accounts': len(self.accounts),
            'tokens': [t.to_dict() for t in self.tokens.values()],
            'accounts': [account.to_dict() for account in self.accounts.values()]
        }
    
    def get_token_info(self, token: str) -> Optional[TokenInfo]:
        """获取Token信息"""
        return self.tokens.get(token)


# 全局账号池实例
_global_account_pool: Optional[AccountPool] = None
_account_pool_lock = asyncio.Lock()


async def get_account_pool() -> AccountPool:
    """获取全局账号池（线程安全）"""
    global _global_account_pool
    if _global_account_pool is None:
        async with _account_pool_lock:
            if _global_account_pool is None:
                _global_account_pool = AccountPool()
                await _global_account_pool.init()
    return _global_account_pool


async def init_account_pool(storage_file: Optional[str] = None, accounts_file: Optional[str] = None) -> AccountPool:
    """初始化全局账号池"""
    global _global_account_pool
    async with _account_pool_lock:
        if _global_account_pool is None:
            _global_account_pool = AccountPool(storage_file=storage_file, accounts_file=accounts_file)
            await _global_account_pool.init()
    return _global_account_pool
