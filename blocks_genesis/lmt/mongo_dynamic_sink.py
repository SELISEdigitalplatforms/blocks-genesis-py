import asyncio
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from bson import BSON, ObjectId
import structlog

class MongoDBDynamicSink:
    def __init__(self, service_name: str, mongo_uri: str, db_name: str, batch_size: int = 20, flush_interval: int = 5):
        self.service_name = service_name
        self.client = AsyncIOMotorClient(mongo_uri)
        self.collection = self.client[db_name][service_name]
        self.batch = []
        self.lock = asyncio.Lock()
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.loop = asyncio.get_event_loop()
        self._start_background_flush()

    def _start_background_flush(self):
        self.loop.create_task(self._flush_periodically())

    async def emit(self, log_event: dict):
        async with self.lock:
            self.batch.append(log_event)
            if len(self.batch) >= self.batch_size:
                await self.flush()

    async def flush(self):
        if not self.batch:
            return
        try:
            await self.collection.insert_many(self.batch)
            self.batch.clear()
        except Exception as e:
            print(f"Failed to flush logs: {e}")

    async def _flush_periodically(self):
        while True:
            await asyncio.sleep(self.flush_interval)
            async with self.lock:
                await self.flush()

    def create_log_document(self, event_dict):
        document = {
            "Timestamp": datetime.utcnow(),
            "Message": event_dict.get("event"),
            "Level": event_dict.get("level").upper(),
            "Exception": event_dict.get("exception", ""),
            "ServiceName": self.service_name,
        }

        # Attach all other structlog properties
        for key, value in event_dict.items():
            if key not in ["event", "level", "exception"]:
                document[key] = value

        return document
