import logging
import pytest
from unittest.mock import MagicMock, patch
from opentelemetry.trace import StatusCode
from blocks_genesis._lmt.activity import Activity
from blocks_genesis._lmt import tracing, log_config

AC = 'blocks_genesis._lmt.activity.'
TR = 'blocks_genesis._lmt.tracing.'
LC = 'blocks_genesis._lmt.log_config.'


def _act():
    a = Activity.__new__(Activity)
    a._span = MagicMock(); a._span.is_recording.return_value = True
    a._root_attributes = {'k': 'v'}; a._token = MagicMock()
    return a


@patch(TR + 'get_blocks_secret')
@patch(TR + 'MongoDBTraceExporter')
@patch(TR + 'BatchSpanProcessor')
@patch(TR + 'Resource')
@patch(TR + 'TracerProvider')
@patch(TR + 'trace')
def test_configure_tracing(mock_trace, mock_tp, mock_res, mock_bsp, mock_exp, mock_gs):
    tracing.configure_tracing()
    mock_trace.set_tracer_provider.assert_called()


def test_configure_logger():
    root = logging.getLogger(); saved = root.handlers[:]; lvl = root.level
    try:
        with patch(LC + 'MongoHandler'), patch(LC + 'TraceContextFilter'):
            log_config.configure_logger()
            assert len(root.handlers) == 2
    finally:
        root.handlers[:] = saved; root.setLevel(lvl)


@patch(AC + 'attach')
@patch(AC + 'trace')
@patch(AC + '_tracer')
@patch(AC + 'get_current_span')
@patch(AC + 'get_current')
def test_activity_init_with_baggage(mock_gc, mock_gcs, mock_tracer, mock_trace, mock_attach):
    parent = MagicMock(); parent.attributes = {'baggage.TenantId': 'tid'}
    mock_gcs.return_value = parent
    ns = MagicMock(); ns.is_recording.return_value = True
    mock_tracer.start_span.return_value = ns
    Activity('test')
    ns.set_attribute.assert_called()


@patch(AC + 'attach')
@patch(AC + 'trace')
@patch(AC + '_tracer')
@patch(AC + 'get_current_span')
@patch(AC + 'get_current')
def test_activity_start_non_recording(mock_gc, mock_gcs, mock_tracer, mock_trace, mock_attach):
    parent = MagicMock(); parent.attributes = {}
    mock_gcs.return_value = parent
    ns = MagicMock(); ns.is_recording.return_value = False
    mock_tracer.start_span.return_value = ns
    assert isinstance(Activity.start('n'), Activity)


def test_find_root_attributes_none():
    assert _act()._find_root_attributes(None) == {}


def test_find_root_attributes_no_attr():
    assert _act()._find_root_attributes(MagicMock(spec=[])) == {}


def test_find_root_attributes_baggage():
    span = MagicMock(); span.attributes = {'baggage.X': 1, 'other': 2}
    assert _act()._find_root_attributes(span) == {'X': 1}


def test_find_root_attributes_no_baggage():
    span = MagicMock(); span.attributes = {'a': 1}
    assert _act()._find_root_attributes(span) == {'a': 1}


def test_get_root_attribute():
    a = _act()
    assert a.get_root_attribute('k') == 'v'
    assert a.get_root_attribute('missing') is None


def test_get_all_root_attributes():
    assert _act().get_all_root_attributes() == {'k': 'v'}
    a = _act(); a._root_attributes = None
    assert a.get_all_root_attributes() == {}


def test_set_property():
    a = _act(); a.set_property('x', 1); a._span.set_attribute.assert_called_with('x', 1)
    a2 = _act(); a2._span.is_recording.return_value = False; a2.set_property('x', 1)


def test_set_properties():
    _act().set_properties({'x': 1, 'y': 2})
    a2 = _act(); a2._span.is_recording.return_value = False; a2.set_properties({'x': 1})


def test_set_status():
    a = _act(); a.set_status(StatusCode.OK, 'ok'); a._span.set_status.assert_called()


def test_stop():
    with patch(AC + 'detach') as md:
        a = _act(); a.stop(); a._span.end.assert_called(); md.assert_called()


def test_enter_exit_no_exception():
    with patch(AC + 'detach'):
        a = _act()
        with a as x:
            assert x is a


def test_enter_exit_with_exception():
    with patch(AC + 'detach'):
        a = _act()
        with pytest.raises(ValueError):
            with a:
                raise ValueError('boom')
        a._span.record_exception.assert_called()


@patch(AC + 'get_current_span')
def test_current(mock_gcs):
    mock_gcs.return_value = 'span'
    assert Activity.current() == 'span'


@patch(AC + 'get_current_span')
def test_get_trace_id(mock_gcs):
    span = MagicMock(); span.get_span_context.return_value.trace_id = 123
    mock_gcs.return_value = span
    assert Activity.get_trace_id() == format(123, '032x')


@patch(AC + 'get_current_span')
def test_get_trace_id_no_span(mock_gcs):
    mock_gcs.return_value = None
    assert Activity.get_trace_id() == ''


@patch(AC + 'get_current_span')
def test_get_span_id(mock_gcs):
    span = MagicMock(); span.get_span_context.return_value.span_id = 456
    mock_gcs.return_value = span
    assert Activity.get_span_id() == format(456, '016x')


@patch(AC + 'get_current_span')
def test_get_span_id_no_span(mock_gcs):
    mock_gcs.return_value = None
    assert Activity.get_span_id() == ''


@patch(AC + 'get_current_span')
def test_set_current_property(mock_gcs):
    span = MagicMock(); span.is_recording.return_value = True
    mock_gcs.return_value = span
    Activity.set_current_property('k', 1); span.set_attribute.assert_called_with('k', 1)


@patch(AC + 'get_current_span')
def test_set_current_property_not_recording(mock_gcs):
    span = MagicMock(); span.is_recording.return_value = False
    mock_gcs.return_value = span
    Activity.set_current_property('k', 1)


@patch(AC + 'get_current_span')
def test_set_current_properties(mock_gcs):
    span = MagicMock(); span.is_recording.return_value = True
    mock_gcs.return_value = span
    Activity.set_current_properties({'k': 1, 'j': 2})


@patch(AC + 'get_current_span')
def test_set_current_status(mock_gcs):
    span = MagicMock(); span.is_recording.return_value = True
    mock_gcs.return_value = span
    Activity.set_current_status(StatusCode.OK, 'ok'); span.set_status.assert_called()


@patch(AC + 'get_current_span')
def test_set_current_properties_not_recording(mock_gcs):
    span = MagicMock(); span.is_recording.return_value = False
    mock_gcs.return_value = span
    Activity.set_current_properties({'k': 1})


@patch(AC + 'get_current_span')
def test_set_current_status_not_recording(mock_gcs):
    span = MagicMock(); span.is_recording.return_value = False
    mock_gcs.return_value = span
    Activity.set_current_status(StatusCode.OK)
