# clients/redis_client.py
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable, Any
import redis
import redis.asyncio as aioredis


class RedisClient(CacheClient):
    """Redis client implementation"""
    
    def __init__(self):
        """Initialize Redis client"""
        self._connection_string = blocks_secret.cache_connection_string
        self._activity_source = activity_source
        self._subscriptions: Dict[str, Callable] = {}
        self._disposed = False
        self._pubsub_tasks: Dict[str, asyncio.Task] = {}
        
        # Parse connection string and initialize clients
        self._redis_config = self._parse_connection_string(blocks_secret.cache_connection_string)
        self._sync_client = redis.Redis(**self._redis_config)
        self._async_client: Optional[aioredis.Redis] = None
        
        # Logger
        self._logger = logging.getLogger(__name__)
    
    def _parse_connection_string(self, connection_string: str) -> Dict[str, Any]:
        """Parse Redis connection string"""
        if connection_string.startswith('redis://'):
            return redis.connection.parse_url(connection_string)
        else:
            # Handle custom format: host=localhost,port=6379,db=0,password=secret
            config = {
                'host': 'localhost',
                'port': 6379,
                'db': 0,
                'decode_responses': True
            }
            
            parts = connection_string.split(',')
            for part in parts:
                if '=' in part:
                    key, value = part.split('=', 1)
                    key = key.strip().lower()
                    value = value.strip()
                    
                    if key == 'host':
                        config['host'] = value
                    elif key == 'port':
                        config['port'] = int(value)
                    elif key == 'db':
                        config['db'] = int(value)
                    elif key == 'password':
                        config['password'] = value
            
            return config
    
    async def _get_async_client(self) -> aioredis.Redis:
        """Get or create async Redis client"""
        if self._async_client is None:
            self._async_client = aioredis.Redis(**self._redis_config)
        return self._async_client
    
    def cache_database(self) -> redis.Redis:
        """Get the Redis database instance"""
        return self._sync_client
    
    def _set_activity(self, key: str, operation: str) -> ActivityContext:
        """Set activity for tracing"""
        context = BlocksContext.get_context()
        activity = self._activity_source.start_activity(f"Redis::{operation}", "Producer")
        
        if context and context.tenant_id:
            activity.set_custom_property("TenantId", context.tenant_id)
        activity.set_tag("Key", key)
        
        return activity
    
    # Synchronous Methods
    def key_exists(self, key: str) -> bool:
        """Check if key exists"""
        with self._set_activity(key, "KeyExists") as activity:
            try:
                result = self._sync_client.exists(key) > 0
                return result
            except Exception as ex:
                activity.set_tag("error", True)
                activity.set_tag("errorMessage", str(ex))
                raise
    
    def add_string_value(self, key: str, value: str, key_life_span: Optional[int] = None) -> bool:
        """Add string value to cache"""
        with self._set_activity(key, "AddStringValue") as activity:
            try:
                if key_life_span is not None:
                    result = self._sync_client.setex(key, key_life_span, value)
                else:
                    result = self._sync_client.set(key, value)
                return bool(result)
            except Exception as ex:
                activity.set_tag("error", True)
                activity.set_tag("errorMessage", str(ex))
                raise
    
    def get_string_value(self, key: str) -> Optional[str]:
        """Get string value from cache"""
        with self._set_activity(key, "GetStringValue") as activity:
            try:
                result = self._sync_client.get(key)
                return result
            except Exception as ex:
                activity.set_tag("error", True)
                activity.set_tag("errorMessage", str(ex))
                raise
    
    def remove_key(self, key: str) -> bool:
        """Remove key from cache"""
        with self._set_activity(key, "RemoveKey") as activity:
            try:
                result = self._sync_client.delete(key) > 0
                return result
            except Exception as ex:
                activity.set_tag("error", True)
                activity.set_tag("errorMessage", str(ex))
                raise
    
    def add_hash_value(self, key: str, value: Dict[str, Any], key_life_span: Optional[int] = None) -> bool:
        """Add hash value to cache"""
        with self._set_activity(key, "AddHashValue") as activity:
            try:
                self._sync_client.hset(key, mapping=value)
                if key_life_span is not None:
                    expire_time = datetime.utcnow() + timedelta(seconds=key_life_span)
                    result = self._sync_client.expireat(key, expire_time)
                    return bool(result)
                return True
            except Exception as ex:
                activity.set_tag("error", True)
                activity.set_tag("errorMessage", str(ex))
                raise
    
    def get_hash_value(self, key: str) -> Dict[str, Any]:
        """Get hash value from cache"""
        with self._set_activity(key, "GetHashValue") as activity:
            try:
                result = self._sync_client.hgetall(key)
                return dict(result) if result else {}
            except Exception as ex:
                activity.set_tag("error", True)
                activity.set_tag("errorMessage", str(ex))
                raise
    
    # Asynchronous Methods
    async def key_exists_async(self, key: str) -> bool:
        """Check if key exists (async)"""
        client = await self._get_async_client()
        with self._set_activity(key, "KeyExists") as activity:
            try:
                result = await client.exists(key) > 0
                return result
            except Exception as ex:
                activity.set_tag("error", True)
                activity.set_tag("errorMessage", str(ex))
                raise
    
    async def add_string_value_async(self, key: str, value: str, key_life_span: Optional[int] = None) -> bool:
        """Add string value to cache (async)"""
        client = await self._get_async_client()
        with self._set_activity(key, "AddStringValue") as activity:
            try:
                if key_life_span is not None:
                    result = await client.setex(key, key_life_span, value)
                else:
                    result = await client.set(key, value)
                return bool(result)
            except Exception as ex:
                activity.set_tag("error", True)
                activity.set_tag("errorMessage", str(ex))
                raise
    
    async def get_string_value_async(self, key: str) -> Optional[str]:
        """Get string value from cache (async)"""
        client = await self._get_async_client()
        with self._set_activity(key, "GetStringValue") as activity:
            try:
                result = await client.get(key)
                return result
            except Exception as ex:
                activity.set_tag("error", True)
                activity.set_tag("errorMessage", str(ex))
                raise
    
    async def remove_key_async(self, key: str) -> bool:
        """Remove key from cache (async)"""
        client = await self._get_async_client()
        with self._set_activity(key, "RemoveKey") as activity:
            try:
                result = await client.delete(key) > 0
                return result
            except Exception as ex:
                activity.set_tag("error", True)
                activity.set_tag("errorMessage", str(ex))
                raise
    
    async def add_hash_value_async(self, key: str, value: Dict[str, Any], key_life_span: Optional[int] = None) -> bool:
        """Add hash value to cache (async)"""
        client = await self._get_async_client()
        with self._set_activity(key, "AddHashValue") as activity:
            try:
                await client.hset(key, mapping=value)
                if key_life_span is not None:
                    expire_time = datetime.utcnow() + timedelta(seconds=key_life_span)
                    result = await client.expireat(key, expire_time)
                    return bool(result)
                return True
            except Exception as ex:
                activity.set_tag("error", True)
                activity.set_tag("errorMessage", str(ex))
                raise
    
    async def get_hash_value_async(self, key: str) -> Dict[str, Any]:
        """Get hash value from cache (async)"""
        client = await self._get_async_client()
        with self._set_activity(key, "GetHashValue") as activity:
            try:
                result = await client.hgetall(key)
                return dict(result) if result else {}
            except Exception as ex:
                activity.set_tag("error", True)
                activity.set_tag("errorMessage", str(ex))
                raise
    
    # Pub/Sub Methods
    async def publish_async(self, channel: str, message: str) -> int:
        """Publish message to channel"""
        if not channel:
            raise ValueError("Channel cannot be empty")
        
        client = await self._get_async_client()
        with self._set_activity(channel, "Publish") as activity:
            try:
                result = await client.publish(channel, message)
                return result
            except Exception as ex:
                activity.set_tag("error", True)
                activity.set_tag("errorMessage", str(ex))
                raise
    
    async def subscribe_async(self, channel: str, handler: Callable[[str, str], None]) -> None:
        """Subscribe to channel with handler"""
        if not channel:
            raise ValueError("Channel cannot be empty")
        if handler is None:
            raise ValueError("Handler cannot be None")
        
        client = await self._get_async_client()
        with self._set_activity(channel, "Subscribe") as activity:
            try:
                # Store the handler
                self._subscriptions[channel] = handler
                
                # Create pubsub instance
                pubsub = client.pubsub()
                await pubsub.subscribe(channel)
                
                # Create task to handle messages
                task = asyncio.create_task(self._handle_subscription(pubsub, channel, handler))
                self._pubsub_tasks[channel] = task
                
            except Exception as ex:
                activity.set_tag("error", True)
                activity.set_tag("errorMessage", str(ex))
                self._subscriptions.pop(channel, None)
                raise
    
    async def unsubscribe_async(self, channel: str) -> None:
        """Unsubscribe from channel"""
        if not channel:
            raise ValueError("Channel cannot be empty")
        
        with self._set_activity(channel, "Unsubscribe") as activity:
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
                
            except Exception as ex:
                activity.set_tag("error", True)
                activity.set_tag("errorMessage", str(ex))
                raise
    
    async def _handle_subscription(self, pubsub: aioredis.client.PubSub, channel: str, handler: Callable[[str, str], None]):
        """Handle subscription messages"""
        try:
            async for message in pubsub.listen():
                if message['type'] == 'message':
                    with self._activity_source.start_activity("Redis::MessageReceived", "Consumer") as message_activity:
                        message_activity.set_tag("Channel", channel)
                        try:
                            channel_name = message['channel']
                            if isinstance(channel_name, bytes):
                                channel_name = channel_name.decode('utf-8')
                            
                            message_data = message['data']
                            if isinstance(message_data, bytes):
                                message_data = message_data.decode('utf-8')
                            
                            handler(channel_name, message_data)
                        except Exception as ex:
                            message_activity.set_tag("error", True)
                            message_activity.set_tag("errorMessage", str(ex))
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