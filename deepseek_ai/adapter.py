"""DeepSeek AI Adapter for chat.deepseek.com - Based on Chat2API logic"""

import json
import uuid
import time
import os
import requests
from typing import Dict, Optional, Tuple, Any

from .proxy_adapter import ProxyManager, get_proxy_manager, init_proxy_manager
from .pow_solver import calculate_challenge_answer


class DeepSeekAdapter:
    """DeepSeek AI Adapter for chat.deepseek.com"""
    
    DEEPSEEK_API_BASE = 'https://chat.deepseek.com/api'
    
    DEFAULT_HEADERS = {
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
        'Origin': 'https://chat.deepseek.com',
        'Referer': 'https://chat.deepseek.com/',
        'Sec-Ch-Ua': '"Microsoft Edge";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0',
        'X-App-Version': '20241129.1',
        'X-Client-Locale': 'zh_CN',
        'X-Client-Platform': 'web',
        'X-Client-Version': '1.8.0',
        'X-Client-Timezone-Offset': '28800',
    }
    
    MODEL_ALIASES = {
        'deepseek-v4-flash': 'deepseek-chat',
        'deepseek-v4-pro': 'deepseek-reasoner',
    }
    
    def __init__(self, token: str, use_proxy: bool = True):
        """Initialize DeepSeek Adapter"""
        self.token = token
        self._access_token: Optional[str] = None
        self._token_expires_at: int = 0
        self._session_id: Optional[str] = None
        self._session_created_at: int = 0
        self.use_proxy = use_proxy
        
        if use_proxy:
            self.proxy_manager = get_proxy_manager()
            if not hasattr(self.proxy_manager, '_initialized') or not self.proxy_manager._initialized:
                self.proxy_manager = init_proxy_manager()
                self.proxy_manager._initialized = True
            self.session = self.proxy_manager.create_session(use_vless=True)
        else:
            self.session = requests.Session()
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            
            adapter = HTTPAdapter(
                pool_connections=10,
                pool_maxsize=10,
                max_retries=Retry(
                    total=3,
                    backoff_factor=0.5,
                    status_forcelist=[500, 502, 503, 504]
                )
            )
            self.session.mount('https://', adapter)
            self.session.mount('http://', adapter)
        
        self.session.timeout = 120
    
    def _uuid(self) -> str:
        """Generate UUID"""
        return str(uuid.uuid4())
    
    def get_headers(self, extra_headers: Optional[Dict] = None) -> Dict[str, str]:
        """Get request headers"""
        headers = self.DEFAULT_HEADERS.copy()
        if extra_headers:
            headers.update(extra_headers)
        return headers
    
    def map_model(self, openai_model: str) -> str:
        """Map OpenAI model name to DeepSeek model name"""
        model = openai_model.lower()
        
        # Remove suffixes for mapping
        base_model = model.replace('-think', '').replace('-fast', '')
        
        if base_model in self.MODEL_ALIASES:
            return self.MODEL_ALIASES[base_model]
        
        return base_model
    
    def acquire_token(self) -> str:
        """Acquire access token from DeepSeek"""
        if not self.token:
            raise ValueError('DeepSeek token not configured')
        
        if self._access_token and self._token_expires_at > int(time.time()):
            return self._access_token
        
        url = f'{self.DEEPSEEK_API_BASE}/v0/users/current'
        response = self.session.get(
            url,
            headers={
                'Authorization': f'Bearer {self.token}',
                **self.get_headers()
            },
            timeout=15
        )
        
        if response.status_code in [401, 403]:
            raise ValueError('Token invalid or expired, please get a new token')
        
        if response.status_code != 200:
            raise ValueError(f'Failed to acquire token: HTTP {response.status_code}')
        
        data = response.json()
        biz_data = data.get('data', {}).get('biz_data') or data.get('biz_data')
        
        if not biz_data or not biz_data.get('token'):
            error_msg = data.get('msg') or data.get('data', {}).get('biz_msg') or 'Unknown error'
            raise ValueError(f'Failed to acquire token: {error_msg}')
        
        self._access_token = biz_data['token']
        self._token_expires_at = int(time.time()) + 3600
        
        return self._access_token
    
    def create_session(self) -> str:
        """Create a new chat session"""
        if self._session_id and (time.time() - self._session_created_at) < 300:
            return self._session_id
        
        token = self.acquire_token()
        
        url = f'{self.DEEPSEEK_API_BASE}/v0/chat_session/create'
        response = self.session.post(
            url,
            json={'character_id': None},
            headers={
                'Authorization': f'Bearer {token}',
                **self.get_headers(),
            },
            timeout=15
        )
        
        data = response.json()
        biz_data = data.get('data', {}).get('biz_data') or data.get('biz_data')
        
        if response.status_code != 200 or not biz_data:
            raise ValueError(f'Failed to create session: {data.get("msg") or response.status_code}')
        
        if 'chat_session' in biz_data and isinstance(biz_data['chat_session'], dict):
            session_id = biz_data['chat_session'].get('id')
        else:
            session_id = biz_data.get('id')
        
        if not session_id:
            raise ValueError(f'Failed to create session: no session id in response')
        
        self._session_id = session_id
        self._session_created_at = time.time()
        
        return self._session_id
    
    def delete_session(self, session_id: str) -> bool:
        """Delete a chat session"""
        try:
            token = self.acquire_token()
            url = f'{self.DEEPSEEK_API_BASE}/v0/chat_session/delete'
            response = self.session.post(
                url,
                json={'chat_session_id': session_id},
                headers={
                    'Authorization': f'Bearer {token}',
                    **self.get_headers(),
                },
                timeout=15
            )
            
            data = response.json()
            success = response.status_code == 200 and data.get('code') == 0
            
            if success and self._session_id == session_id:
                self._session_id = None
            
            return success
        except Exception:
            return False
    
    def get_challenge(self, target_path: str) -> Dict:
        """Get POW challenge from DeepSeek"""
        token = self.acquire_token()
        url = f'{self.DEEPSEEK_API_BASE}/v0/chat/create_pow_challenge'
        response = self.session.post(
            url,
            json={'target_path': target_path},
            headers={
                'Authorization': f'Bearer {token}',
                **self.get_headers(),
            },
            timeout=15
        )
        
        data = response.json()
        biz_data = data.get('data', {}).get('biz_data') or data.get('biz_data')
        
        if response.status_code != 200 or not biz_data or not biz_data.get('challenge'):
            raise ValueError(f'Failed to get challenge: {data.get("msg") or response.status_code}')
        
        return biz_data['challenge']
    
    def _calculate_challenge_answer(self, challenge: Dict) -> str:
        """Calculate challenge answer using DeepSeekHashV1 WASM"""
        try:
            answer = calculate_challenge_answer(challenge)
            return answer
        except Exception as e:
            raise ValueError(f'Failed to calculate challenge answer: {e}')
    
    def _messages_to_prompt(self, messages: list) -> str:
        """Convert messages to DeepSeek prompt format"""
        processed_messages = []
        
        for message in messages:
            role = message.get('role', '')
            content = message.get('content', '')
            
            if role == 'assistant' and message.get('tool_calls'):
                tool_calls_text = []
                for tc in message['tool_calls']:
                    func = tc.get('function', {})
                    tool_calls_text.append(f'<tool_calling>\n<name>{func.get("name", "")}</name>\n<arguments>{func.get("arguments", "")}</arguments>\n</tool_calling>')
                text = '\n'.join(tool_calls_text)
            elif role == 'tool' and message.get('tool_call_id'):
                text = f'<tool_response tool_call_id="{message["tool_call_id"]}">\n{content}\n</tool_response>'
            elif isinstance(content, list):
                texts = [item.get('text', '') for item in content if item.get('type') == 'text']
                text = '\n'.join(texts)
            else:
                text = str(content or '')
            
            processed_messages.append({'role': role, 'text': text})
        
        if not processed_messages:
            return ''
        
        merged_blocks = []
        current_block = {**processed_messages[0]}
        
        for i in range(1, len(processed_messages)):
            msg = processed_messages[i]
            if msg['role'] == current_block['role']:
                current_block['text'] += f"\n\n{msg['text']}"
            else:
                merged_blocks.append(current_block)
                current_block = {**msg}
        merged_blocks.append(current_block)
        
        result = []
        for index, block in enumerate(merged_blocks):
            if block['role'] == 'assistant':
                result.append(f"<｜Assistant｜>{block['text']}<｜end of sentence｜>")
            elif block['role'] in ['user', 'system']:
                result.append(f"<｜User｜>{block['text']}" if index > 0 else block['text'])
            elif block['role'] == 'tool':
                result.append(f"<｜User｜>{block['text']}")
        
        prompt = ''.join(result)
        import re
        prompt = re.sub(r'!\[.+\]\(.+\)', '', prompt)
        
        return prompt
    
    def chat_completion(
        self,
        model: str,
        messages: list,
        stream: bool = True,
        temperature: Optional[float] = None,
        web_search: bool = False,
        reasoning_effort: Optional[str] = None,
        thinking_enabled: Optional[bool] = None
    ) -> Tuple[requests.Response, str]:
        """Send chat completion request"""
        token = self.acquire_token()
        session_id = self.create_session()
        
        challenge = self.get_challenge('/api/v0/chat/completion')
        challenge_answer = self._calculate_challenge_answer(challenge)
        
        prompt = self._messages_to_prompt(messages)
        
        search_enabled = web_search
        
        # Determine thinking mode
        if thinking_enabled is None:
            # Auto-determine from model name or reasoning_effort
            model_lower = model.lower()
            if '-think' in model_lower:
                thinking_enabled = True
            elif '-fast' in model_lower:
                thinking_enabled = False
            elif reasoning_effort:
                thinking_enabled = True
            else:
                # Default: flash = no thinking, pro = thinking
                thinking_enabled = 'pro' in model_lower
        
        url = f'{self.DEEPSEEK_API_BASE}/v0/chat/completion'
        
        model_type = "expert" if thinking_enabled else "default"
        
        payload = {
            'chat_session_id': session_id,
            'parent_message_id': None,
            'model_type': model_type,
            'prompt': prompt,
            'ref_file_ids': [],
            'thinking_enabled': thinking_enabled,
            'search_enabled': search_enabled,
            'preempt': False,
        }
        
        response = self.session.post(
            url,
            json=payload,
            headers={
                'Authorization': f'Bearer {token}',
                **self.get_headers(),
                'X-Ds-Pow-Response': challenge_answer,
            },
            stream=True,
            timeout=120
        )
        
        if response.status_code != 200:
            raise ValueError(f'Chat completion failed: HTTP {response.status_code}')
        
        return response, session_id
    
    @staticmethod
    def is_deepseek_provider(api_endpoint: str) -> bool:
        """Check if the API endpoint is DeepSeek"""
        return 'deepseek.com' in api_endpoint
