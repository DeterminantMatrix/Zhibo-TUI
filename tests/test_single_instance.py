import threading
import uuid

from zhibo.single_instance import COMMAND_SHOW, SingleInstance, notify_existing_instance


def test_single_instance_acknowledges_and_dispatches_command():
    received = []
    delivered = threading.Event()
    instance = SingleInstance(key=f"test-{uuid.uuid4()}")
    instance.set_command_handler(lambda command: (received.append(command), delivered.set()))
    try:
        assert instance.acquire() is True
        assert notify_existing_instance(COMMAND_SHOW, key=instance.key) is True
        assert delivered.wait(timeout=1)
        assert received == [COMMAND_SHOW]
    finally:
        instance.close()
