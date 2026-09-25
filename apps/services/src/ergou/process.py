"""Own a worker and its entire descendant process tree."""

import asyncio
import os
import signal
import subprocess
import sys


class WorkerProcess:
    def __init__(self, process, job=None):
        self.process = process
        self.job = job

    @classmethod
    async def start(cls, payload, module="ergou.worker"):
        import json

        options = (
            {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            module,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=2 * 1024 * 1024,
            **options,
        )
        wrapper = cls(process)
        try:
            if os.name == "nt":
                import win32api
                import win32con
                import win32job

                wrapper.job = win32job.CreateJobObject(None, "")
                info = win32job.QueryInformationJobObject(
                    wrapper.job, win32job.JobObjectExtendedLimitInformation
                )
                info["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                win32job.SetInformationJobObject(
                    wrapper.job, win32job.JobObjectExtendedLimitInformation, info
                )
                handle = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, process.pid)
                try:
                    win32job.AssignProcessToJobObject(wrapper.job, handle)
                finally:
                    handle.Close()
            # Worker does not perform any work before this message; job assignment is complete first.
            process.stdin.write((json.dumps(payload) + "\n").encode())
            await process.stdin.drain()
            process.stdin.close()
            return wrapper
        except BaseException:
            await wrapper.stop()
            raise

    async def stop(self):
        if self.job is not None:
            import win32job

            try:
                win32job.TerminateJobObject(self.job, 1)
                # Termination is asynchronous. Wait for descendants too, before callers unlink media.
                async with asyncio.timeout(10):
                    while win32job.QueryInformationJobObject(
                        self.job, win32job.JobObjectBasicAccountingInformation
                    )["ActiveProcesses"]:
                        await asyncio.sleep(0.01)
            finally:
                self.job.Close()
                self.job = None
        elif self.process.returncode is None:
            if os.name == "nt":
                self.process.kill()
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        await self.process.wait()

    def close(self):
        if self.job is not None:
            self.job.Close()
            self.job = None
