import pytest
from unittest.mock import patch, MagicMock
from blocks_genesis._database.mongo_event_subscriber import MongoEventSubscriber

@patch('blocks_genesis._database.mongo_event_subscriber.Activity')
@patch('blocks_genesis._database.mongo_event_subscriber.BlocksContextManager')
def test_started_and_succeeded_and_failed(mock_ctx_mgr, mock_activity):
    sub = MongoEventSubscriber()
    # Mock event
    event = MagicMock()
    event.command_name = 'find'
    event.request_id = 1
    # started
    activity_instance = MagicMock()
    mock_activity.start.return_value = activity_instance
    sub.started(event)
    assert sub._activities[event.request_id] == activity_instance
    # succeeded
    event.duration_micros = 1000
    sub.succeeded(event)
    activity_instance.set_property.assert_called_with('db.duration_ms', 1.0)
    activity_instance.set_status.assert_called()
    activity_instance.stop.assert_called()
    # failed
    event2 = MagicMock()
    event2.request_id = 2
    event2.command_name = 'insert'
    event2.duration_micros = 2000
    event2.failure = 'failmsg'
    mock_activity2 = MagicMock()
    sub._activities[event2.request_id] = mock_activity2
    sub.failed(event2)
    mock_activity2.set_property.assert_any_call('db.duration_ms', 2.0)
    mock_activity2.set_property.assert_any_call('exception.message', str(event2.failure))
    mock_activity2.set_status.assert_called()
    mock_activity2.stop.assert_called() 