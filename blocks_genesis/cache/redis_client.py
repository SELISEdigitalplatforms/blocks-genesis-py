import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable, Any
import redis
import redis.asyncio as aioredis
from opentelemetry.trace import StatusCode

from blocks_genesis.auth.blocks_context import BlocksContextManager
from blocks_genesis.cache.CacheClient import CacheClient
from blocks_genesis.core.secret_loader import get_blocks_secret
from blocks_genesis.lmt.activity import Activity


class RedisClient(CacheClient):
    """Redis client implementation with Activity tracing"""
    
    def __init__(self):
        """Initialize Redis client"""
        self._blocks_secret = get_blocks_secret()
        self._connection_string = self._blocks_secret.CacheConnectionString
        self._subscriptions: Dict[str, Callable] = {}
        self._disposed = False
        self._pubsub_tasks: Dict[str, asyncio.Task] = {}
        
        # Parse connection string and initialize clients
        self._redis_config = self._parse_connection_string(self._blocks_secret.CacheConnectionString)
        self._sync_client = redis.Redis(**self._redis_config)
        self._async_client: Optional[aioredis.Redis] = None
    
    def _parse_connection_string(self, connection_string: str) -> Dict[str, Any]:
        parts = connection_string.split(',', 1)
        host_port = parts[0]  # e.g. "hostname:6379"
        
        query = ""
        if len(parts) > 1:
            query = parts[1].replace(',', '&')
        
        url = f"redis://{host_port}"
        if query:
            url += f"/?{query}"
        
        config = redis.connection.parse_url(url)

        # Allowed keys for redis.Redis constructor
        allowed_keys = {
            'host', 'port', 'username', 'password', 'db', 'ssl', 
            'socket_connect_timeout', 'socket_timeout',
            'encoding', 'encoding_errors', 'decode_responses',
            'retry_on_timeout', 'max_connections'
        }

        # Map keys like connectTimeout -> socket_connect_timeout (in seconds)
        if 'connectTimeout' in config:
            # convert from ms to seconds
            config['socket_connect_timeout'] = int(config.pop('connectTimeout')) / 1000
        if 'syncTimeout' in config:
            config['socket_timeout'] = int(config.pop('syncTimeout')) / 1000

        # Remove unsupported keys
        config = {k: v for k, v in config.items() if k in allowed_keys}

        return config
 
    async def _get_async_client(self) -> aioredis.Redis:
        """Get or create async Redis client"""
        if self._async_client is None:
            self._async_client = aioredis.Redis(**self._redis_config)
        return self._async_client
    
    def cache_database(self) -> redis.Redis:
        """Get the Redis database instance"""
        return self._sync_client
    
    def _create_activity(self, key: str, operation: str) -> Activity:
        """Create activity for tracing"""
        activity = Activity.start(f"Redis::{operation}")
        activity.set_property("key", key)
        activity.set_property("operation", operation)
        activity.set_property("baggage.TenantId", BlocksContextManager.get_context().tenant_id if BlocksContextManager.get_context() else "miscellaneous")
        return activity
    
    # Synchronous Methods
    def key_exists(self, key: str) -> bool:
        """Check if key exists"""
        with self._create_activity(key, "KeyExists") as activity:
            try:
                result = self._sync_client.exists(key) > 0
                activity.set_property("exists", result)
                return result
            except Exception as ex:
                activity.set_property("error", True)
                activity.set_property("error_message", str(ex))
                activity.set_status(StatusCode.ERROR, str(ex))
                raise
    
    def add_string_value(self, key: str, value: str, key_life_span: Optional[int] = None) -> bool:
        """Add string value to cache"""
        with self._create_activity(key, "AddStringValue") as activity:
            try:
                activity.set_property("value_length", len(value))
                if key_life_span is not None:
                    activity.set_property("ttl", key_life_span)
                    result = self._sync_client.setex(key, key_life_span, value)
                else:
                    result = self._sync_client.set(key, value)
                
                success = bool(result)
                activity.set_property("success", success)
                return success
            except Exception as ex:
                activity.set_property("error", True)
                activity.set_property("error_message", str(ex))
                activity.set_status(StatusCode.ERROR, str(ex))
                raise
    
    def get_string_value(self, key: str) -> Optional[str]:
        """Get string value from cache"""
        with self._create_activity(key, "GetStringValue") as activity:
            try:
                result = self._sync_client.get(key)
                activity.set_property("found", result is not None)
                if result:
                    activity.set_property("value_length", len(result))
                return result
            except Exception as ex:
                activity.set_property("error", True)
                activity.set_property("error_message", str(ex))
                activity.set_status(StatusCode.ERROR, str(ex))
                raise
    
    def remove_key(self, key: str) -> bool:
        """Remove key from cache"""
        with self._create_activity(key, "RemoveKey") as activity:
            try:
                result = self._sync_client.delete(key) > 0
                activity.set_property("deleted", result)
                return result
            except Exception as ex:
                activity.set_property("error", True)
                activity.set_property("error_message", str(ex))
                activity.set_status(StatusCode.ERROR, str(ex))
                raise
    
    def add_hash_value(self, key: str, value: Dict[str, Any], key_life_span: Optional[int] = None) -> bool:
        """Add hash value to cache"""
        with self._create_activity(key, "AddHashValue") as activity:
            try:
                activity.set_property("field_count", len(value))
                if key_life_span is not None:
                    activity.set_property("ttl", key_life_span)
                
                self._sync_client.hset(key, mapping=value)
                if key_life_span is not None:
                    expire_time = datetime.utcnow() + timedelta(seconds=key_life_span)
                    result = self._sync_client.expireat(key, expire_time)
                    success = bool(result)
                else:
                    success = True
                
                activity.set_property("success", success)
                return success
            except Exception as ex:
                activity.set_property("error", True)
                activity.set_property("error_message", str(ex))
                activity.set_status(StatusCode.ERROR, str(ex))
                raise
    
    def get_hash_value(self, key: str) -> Dict[str, Any]:
        """Get hash value from cache"""
        with self._create_activity(key, "GetHashValue") as activity:
            try:
                result = self._sync_client.hgetall(key)
                hash_dict = dict(result) if result else {}
                activity.set_property("found", bool(result))
                activity.set_property("field_count", len(hash_dict))
                return hash_dict
            except Exception as ex:
                activity.set_property("error", True)
                activity.set_property("error_message", str(ex))
                activity.set_status(StatusCode.ERROR, str(ex))
                raise
    
    # Asynchronous Methods
    async def key_exists_async(self, key: str) -> bool:
        """Check if key exists (async)"""
        client = await self._get_async_client()
        with self._create_activity(key, "KeyExists") as activity:
            try:
                result = await client.exists(key) > 0
                activity.set_property("exists", result)
                return result
            except Exception as ex:
                activity.set_property("error", True)
                activity.set_property("error_message", str(ex))
                activity.set_status(StatusCode.ERROR, str(ex))
                raise
    
    async def add_string_value_async(self, key: str, value: str, key_life_span: Optional[int] = None) -> bool:
        """Add string value to cache (async)"""
        client = await self._get_async_client()
        with self._create_activity(key, "AddStringValue") as activity:
            try:
                activity.set_property("value_length", len(value))
                if key_life_span is not None:
                    activity.set_property("ttl", key_life_span)
                    result = await client.setex(key, key_life_span, value)
                else:
                    result = await client.set(key, value)
                
                success = bool(result)
                activity.set_property("success", success)
                return success
            except Exception as ex:
                activity.set_property("error", True)
                activity.set_property("error_message", str(ex))
                activity.set_status(StatusCode.ERROR, str(ex))
                raise
    
    async def get_string_value_async(self, key: str) -> Optional[str]:
        """Get string value from cache (async)"""
        client = await self._get_async_client()
        with self._create_activity(key, "GetStringValue") as activity:
            try:
                result = await client.get(key)
                activity.set_property("found", result is not None)
                if result:
                    activity.set_property("value_length", len(result))
                return result
            except Exception as ex:
                activity.set_property("error", True)
                activity.set_property("error_message", str(ex))
                activity.set_status(StatusCode.ERROR, str(ex))
                raise
    
    async def remove_key_async(self, key: str) -> bool:
        """Remove key from cache (async)"""
        client = await self._get_async_client()
        with self._create_activity(key, "RemoveKey") as activity:
            try:
                result = await client.delete(key) > 0
                activity.set_property("deleted", result)
                return result
            except Exception as ex:
                activity.set_property("error", True)
                activity.set_property("error_message", str(ex))
                activity.set_status(StatusCode.ERROR, str(ex))
                raise
    
    async def add_hash_value_async(self, key: str, value: Dict[str, Any], key_life_span: Optional[int] = None) -> bool:
        """Add hash value to cache (async)"""
        client = await self._get_async_client()
        with self._create_activity(key, "AddHashValue") as activity:
            try:
                activity.set_property("field_count", len(value))
                if key_life_span is not None:
                    activity.set_property("ttl", key_life_span)
                
                await client.hset(key, mapping=value)
                if key_life_span is not None:
                    expire_time = datetime.utcnow() + timedelta(seconds=key_life_span)
                    result = await client.expireat(key, expire_time)
                    success = bool(result)
                else:
                    success = True
                
                activity.set_property("success", success)
                return success
            except Exception as ex:
                activity.set_property("error", True)
                activity.set_property("error_message", str(ex))
                activity.set_status(StatusCode.ERROR, str(ex))
                raise
    
    async def get_hash_value_async(self, key: str) -> Dict[str, Any]:
        """Get hash value from cache (async)"""
        client = await self._get_async_client()
        with self._create_activity(key, "GetHashValue") as activity:
            try:
                result = await client.hgetall(key)
                hash_dict = dict(result) if result else {}
                activity.set_property("found", bool(result))
                activity.set_property("field_count", len(hash_dict))
                return hash_dict
            except Exception as ex:
                activity.set_property("error", True)
                activity.set_property("error_message", str(ex))
                activity.set_status(StatusCode.ERROR, str(ex))
                raise
    
    # Pub/Sub Methods
    async def publish_async(self, channel: str, message: str) -> int:
        """Publish message to channel"""
        if not channel:
            raise ValueError("Channel cannot be empty")
        
        client = await self._get_async_client()
        with self._create_activity(channel, "Publish") as activity:
            try:
                activity.set_property("message_length", len(message))
                result = await client.publish(channel, message)
                activity.set_property("subscribers_notified", result)
                return result
            except Exception as ex:
                activity.set_property("error", True)
                activity.set_property("error_message", str(ex))
                activity.set_status(StatusCode.ERROR, str(ex))
                raise
    
    async def subscribe_async(self, channel: str, handler: Callable[[str, str], None]) -> None:
        """Subscribe to channel with handler"""
        if not channel:
            raise ValueError("Channel cannot be empty")
        if handler is None:
            raise ValueError("Handler cannot be None")
        
        client = await self._get_async_client()
        with self._create_activity(channel, "Subscribe") as activity:
            try:
                # Store the handler
                self._subscriptions[channel] = handler
                
                # Create pubsub instance
                pubsub = client.pubsub()
                await pubsub.subscribe(channel)
                
                # Create task to handle messages
                task = asyncio.create_task(self._handle_subscription(pubsub, channel, handler))
                self._pubsub_tasks[channel] = task
                
                activity.set_property("subscribed", True)
                
            except Exception as ex:
                activity.set_property("error", True)
                activity.set_property("error_message", str(ex))
                activity.set_status(StatusCode.ERROR, str(ex))
                self._subscriptions.pop(channel, None)
                raise
    
    async def unsubscribe_async(self, channel: str) -> None:
        """Unsubscribe from channel"""
        if not channel:
            raise ValueError("Channel cannot be empty")
        
        with self._create_activity(channel, "Unsubscribe") as activity:
            try:
                # Cancel the subscription task
                if channel in self._pubsub_tasks:
                    task = self._pubsub_tasks.pop(channel)
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                
                # Remove from subscriptions
                self._subscriptions.pop(channel, None)
                activity.set_property("unsubscribed", True)
                
            except Exception as ex:
                activity.set_property("error", True)
                activity.set_property("error_message", str(ex))
                activity.set_status(StatusCode.ERROR, str(ex))
                raise
    
    async def _handle_subscription(self, pubsub: aioredis.client.PubSub, channel: str, handler: Callable[[str, str], None]):
        """Handle subscription messages"""
        try:
            async for message in pubsub.listen():
                if message['type'] == 'message':
                    with Activity.start("Redis::MessageReceived") as message_activity:
                        message_activity.set_property("channel", channel)
                        try:
                            channel_name = message['channel']
                            if isinstance(channel_name, bytes):
                                channel_name = channel_name.decode('utf-8')
                            
                            message_data = message['data']
                            if isinstance(message_data, bytes):
                                message_data = message_data.decode('utf-8')
                            
                            message_activity.set_property("message_length", len(message_data))
                            handler(channel_name, message_data)
                            message_activity.set_property("handled", True)
                            
                        except Exception as ex:
                            message_activity.set_property("error", True)
                            message_activity.set_property("error_message", str(ex))
                            message_activity.set_status(StatusCode.ERROR, str(ex))
                            self._logger.error(f"Error handling message in channel {channel}: {ex}")
        except asyncio.CancelledError:
            # Expected when unsubscribing
            pass
        except Exception as ex:
            self._logger.error(f"Error in subscription handler for channel {channel}: {ex}")
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
    
    # Dispose pattern
    def dispose(self) -> None:
        """Dispose resources synchronously"""
        if self._disposed:
            return
        
        # Clean up subscriptions
        for channel in list(self._subscriptions.keys()):
            self._subscriptions.pop(channel, None)
        
        # Close sync client
        if self._sync_client:
            self._sync_client.close()
        
        self._disposed = True
    
    async def dispose_async(self) -> None:
        """Dispose resources asynchronously"""
        if self._disposed:
            return
        
        # Cancel all pubsub tasks
        for channel, task in self._pubsub_tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        self._pubsub_tasks.clear()
        self._subscriptions.clear()
        
        # Close async client
        if self._async_client:
            await self._async_client.close()
        
        # Close sync client
        if self._sync_client:
            self._sync_client.close()
        
        self._disposed = True
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.dispose()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.dispose_async()