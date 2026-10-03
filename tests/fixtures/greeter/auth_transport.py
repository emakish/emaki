# Isolated test copy only: keep the production worker/framing, replace its socket.
import importlib.util, json, os, pathlib, struct, time
spec = importlib.util.spec_from_file_location("production_auth", pathlib.Path(__file__).with_name("greeter-auth-real.py"))
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
profile = pathlib.Path(__file__).parent.parent
mode_path = profile / "fixture-mode"
mode = mode_path.read_text() if mode_path.exists() else "verdict"
class Stream:
    def __init__(self, *_args):
        self.data = bytearray()
        self.delay = 0
        self.exited = False
    def __enter__(self): return self
    def __exit__(self, *_args): pass
    def settimeout(self, _value): pass
    def connect(self, _path): pass
    def sendall(self, frame):
        size, = struct.unpack("=i", frame[:4])
        assert size == len(frame) - 4
        request = json.loads(frame[4:])
        kind = request["type"]
        if kind == "create_session":
            self.delay = .35 if mode == "cancel-before-prompt" else 0
            reply = dict(type="auth_message", auth_message_type="secret", auth_message="Password:")
        elif kind == "post_auth_message_response":
            assert request.pop("response") == "fixture-éЖ🔒"
            request["response_matches"] = True
            if mode == "cancel-after-prompt":
                (profile / "cancel-now").write_text("go")
            self.delay = .35
            if mode == "reject-once" and not (profile / "rejected-once").exists():
                (profile / "rejected-once").write_text("done")
                self.exited = True
                reply = dict(type="error", error_type="auth_error", description="fixture rejection")
            else:
                reply = dict(type="success")
        elif kind == "cancel_session":
            reply = dict(type="error", error_type="error", description="worker exited") if self.exited else dict(type="success")
        else:
            assert kind == "start_session"
            reply = dict(type="success")
        request["pid"] = os.getpid()
        with (profile / "wire.jsonl").open("a") as record:
            record.write(json.dumps(request) + "\n")
        if mode == "cancel-before-prompt" and kind == "create_session":
            (profile / "cancel-now").write_text("go")
        data = json.dumps(reply).encode()
        self.data.extend(struct.pack("=i", len(data)) + data)
    def recv(self, count):
        if self.delay:
            time.sleep(self.delay)
            self.delay = 0
        result = bytes(self.data[:min(count, 2)])
        del self.data[:len(result)]
        return result
worker.socket.socket = Stream
os._exit(worker.entrypoint())
