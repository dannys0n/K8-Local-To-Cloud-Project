"""
Game server: TCP server with authoritative state (open -> running -> stop).
Match config (e.g. duration) is decided by clients (custom match); server notifies
clients on state changes and closes itself when done or when no clients remain in running.
"""
import asyncio
import os
import sys
import time

SESSION_ID = os.getenv("SESSION_ID", "unknown")
PORT = int(os.getenv("PORT", "8080"))

# State: open -> running -> stop (server-authoritative)
_state = "open"
# Match duration in seconds; set by first client via REQUEST_MATCH <sec>
_match_duration_seconds: float | None = None
# When we transitioned to running (time.time())
_running_started_at: float | None = None
_clients: list[asyncio.StreamWriter] = []
_state_lock = asyncio.Lock()
_shutdown = asyncio.Event()


def _get_state() -> str:
    return _state


def _set_state(new: str) -> None:
    global _state
    _state = new


async def _broadcast(line: str) -> None:
    """Send a line to all connected clients (with newline)."""
    msg = (line if line.endswith("\n") else line + "\n").encode()
    async with _state_lock:
        for w in _clients:
            try:
                w.write(msg)
                await w.drain()
            except Exception:  # noqa: BLE001
                pass


async def _run_timer(duration_seconds: float) -> None:
    """After duration_seconds in 'running', transition to stop and trigger shutdown."""
    await asyncio.sleep(duration_seconds)
    async with _state_lock:
        if _get_state() != "running":
            return
        _set_state("stop")
    await _broadcast("STATE stop")
    _shutdown.set()


def _check_empty_and_stop() -> None:
    """If in running state and no clients left, close session early."""
    global _state
    if _state == "running" and len(_clients) == 0:
        _set_state("stop")
        _shutdown.set()


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    global _match_duration_seconds
    async with _state_lock:
        _clients.append(writer)

    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            raw = line.decode().strip()
            cmd = raw.upper()
            if cmd == "GET_STATE":
                writer.write(f"STATE {_get_state()}\n".encode())
                await writer.drain()
            elif cmd == "GET_RUNNING_LENGTH":
                dur = _match_duration_seconds
                if dur is not None:
                    writer.write(f"RUNNING_LENGTH {int(dur)}\n".encode())
                else:
                    writer.write(b"RUNNING_LENGTH 0\n")
                await writer.drain()
            elif cmd.startswith("REQUEST_MATCH "):
                # Client requests custom match duration (seconds). First one wins.
                parts = raw.split()
                if len(parts) == 2:
                    try:
                        sec = float(parts[1])
                        if sec > 0 and sec <= 86400:  # cap 24h
                            started_now = False
                            async with _state_lock:
                                if _match_duration_seconds is None and _get_state() == "open":
                                    _match_duration_seconds = sec
                                    _set_state("running")
                                    global _running_started_at
                                    _running_started_at = time.time()
                                    asyncio.create_task(_run_timer(sec))
                                    started_now = True
                            writer.write(f"RUNNING_LENGTH {int(_match_duration_seconds or 0)}\n".encode())
                            await writer.drain()
                            if started_now:
                                await _broadcast("STATE running")
                            continue
                    except ValueError:
                        pass
                writer.write(b"UNKNOWN\n")
                await writer.drain()
            else:
                writer.write(b"UNKNOWN\n")
                await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    finally:
        async with _state_lock:
            if writer in _clients:
                _clients.remove(writer)
            _check_empty_and_stop()
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


async def _serve() -> None:
    server = await asyncio.start_server(_handle_client, "0.0.0.0", PORT)
    async with server:
        await _shutdown.wait()
    server.close()
    await server.wait_closed()
    # Close all client connections before exiting
    async with _state_lock:
        for w in _clients[:]:
            try:
                w.close()
                await w.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            _clients.clear()


def main() -> int:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_serve())
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
