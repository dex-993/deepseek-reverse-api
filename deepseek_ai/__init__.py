"""DeepSeek AI Reverse API Client

OpenAI compatible API for DeepSeek AI (chat.deepseek.com)
"""

from .adapter import DeepSeekAdapter
from .client import DeepSeekClient
from .stream_handler import DeepSeekStreamHandler
from .tool_parser import ToolParser
from .account_register import (
    DeepSeekAccountRegister,
    RegistrationResult,
    register_account_auto
)
from .subscription import (
    VlessNode,
    Subscription,
    SubscriptionManager,
    get_subscription_manager,
    init_subscriptions_from_env
)
from .node_storage import (
    NodeStorage,
    get_node_storage,
    init_node_storage
)
from .node_tester import (
    TestResult,
    NodeTester,
    get_node_tester,
    init_node_tester
)
from .vless_proxy import (
    VlessProxy,
    VlessProxyPool,
    get_proxy_pool,
    init_proxy_pool_from_env
)
from .proxy_adapter import (
    ProxyManager,
    get_proxy_manager,
    init_proxy_manager
)

__version__ = '1.0.0'

__all__ = [
    # Adapter & Client
    'DeepSeekAdapter',
    'DeepSeekClient',
    'DeepSeekStreamHandler',
    'ToolParser',
    
    # Account Registration
    'DeepSeekAccountRegister',
    'RegistrationResult',
    'register_account_auto',
    
    # Subscription Management
    'VlessNode',
    'Subscription',
    'SubscriptionManager',
    'get_subscription_manager',
    'init_subscriptions_from_env',
    
    # Node Storage
    'NodeStorage',
    'get_node_storage',
    'init_node_storage',
    
    # Node Testing
    'TestResult',
    'NodeTester',
    'get_node_tester',
    'init_node_tester',
    
    # Proxy
    'VlessProxy',
    'VlessProxyPool',
    'get_proxy_pool',
    'init_proxy_pool_from_env',
    'ProxyManager',
    'get_proxy_manager',
    'init_proxy_manager',
]
