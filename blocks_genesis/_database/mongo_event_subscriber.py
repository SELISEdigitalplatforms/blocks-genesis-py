from datetime import datetime
from pymongo.monitoring import CommandListener
from opentelemetry.trace import StatusCode

from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._lmt.activity import Activity


class MongoEventSubscriber(CommandListener):
    def __init__(self):
        self._activities = {}

    def started(self, event):
        activity = Activity.start(f"MongoDb::{event.command_name}")
        activity.set_properties({
            "db.system": "mongodb",
            "db.operation": event.command_name,
            "db.request_id": str(event.request_id),
            "db.timestamp": datetime.now().isoformat(),
            "baggage.TenantId": BlocksContextManager.get_context().tenant_id if BlocksContextManager.get_context() else "miscellaneous"
        })

        self._activities[event.request_id] = activity

    def succeeded(self, event):
        activity = self._activities.pop(event.request_id, None)
        if activity:
            activity.set_property("db.duration_ms", event.duration_micros / 1000)
            activity.set_status(StatusCode.OK)
            activity.stop()

    def failed(self, event):
        activity = self._activities.pop(event.request_id, None)
        if activity:
            activity.set_property("db.duration_ms", event.duration_micros / 1000)
            activity.set_property("exception.message", str(event.failure))
            activity.set_status(StatusCode.ERROR, str(event.failure))
            activity.stop()
