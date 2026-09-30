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


def test_acquire_falls_back_when_first_candidate_port_is_foreign():
    import socket as socket_module

    from zhibo.single_instance import _port_candidates

    key = f"test-{uuid.uuid4()}"
    ports = list(_port_candidates(key))
    # 用一个不说话的外部 socket 模拟残留占用（TIME_WAIT 后继者或无关程序）。
    foreign = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
    foreign.bind(("127.0.0.1", ports[0]))
    foreign.listen(1)
    try:
        instance = SingleInstance(key=key)
        assert instance.acquire() is True, "端口被占时应回退到下一个候选而不是拒绝启动"
        assert instance.port != ports[0]
        try:
            instance.close()
        finally:
            pass
    finally:
        foreign.close()


def test_second_launcher_finds_instance_that_moved_to_backup_port():
    from zhibo.single_instance import _port_candidates

    key = f"test-{uuid.uuid4()}"
    first = SingleInstance(key=key)
    # 模拟首实例绑定在非首选端口（如启动时首选端口被残留占用）；
    # 候选可能落在系统保留端口段（Hyper-V 排除区间），挑一个实际可绑定的。
    import socket as socket_module

    candidates = list(_port_candidates(key))
    server = None
    for candidate in candidates[1:]:
        probe = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", candidate))
        except OSError:
            probe.close()
            continue
        probe.close()
        server = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
        if hasattr(socket_module, "SO_EXCLUSIVEADDRUSE"):
            server.setsockopt(socket_module.SOL_SOCKET, socket_module.SO_EXCLUSIVEADDRUSE, 1)
        try:
            server.bind(("127.0.0.1", candidate))
            server.listen(5)
            first.port = candidate
            break
        except OSError:
            server.close()
            server = None
    assert server is not None, "所有候选端口都无法绑定"
    first.set_command_handler(lambda _command: None)

    first._socket = server
    import threading as threading_module

    first._stopped.clear()
    first._thread = threading_module.Thread(target=first._serve, daemon=True)
    first._thread.start()
    try:
        # 第二个启动者：扫描候选应发现换到 #3 的实例，而不是另起炉灶。
        second = SingleInstance(key=key)
        assert second.acquire() is False
        assert notify_existing_instance(COMMAND_SHOW, key=key) is True
    finally:
        first.close()
        server.close()
