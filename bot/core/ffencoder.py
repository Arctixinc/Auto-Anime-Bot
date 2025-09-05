import os
import re
import signal
from time import time
from math import floor
from os import path as ospath
from asyncio import sleep as asleep, gather, create_task
from asyncio.subprocess import PIPE
from asyncio import to_thread
from aiofiles import open as aiopen
from aiofiles.os import remove as aioremove, rename as aiorename
from asyncio import create_subprocess_shell
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from moviepy.editor import VideoFileClip

from bot import Var, bot_loop, ffpids_cache, LOGS
from .func_utils import convertBytes, convertTime, sendMessage, editMessage
from .reporter import rep

ffargs = {
    '1080': Var.FFCODE_1080,
    '720': Var.FFCODE_720,
    '480': Var.FFCODE_480,
    '360': Var.FFCODE_360,
    '361': Var.FFCODE_361,
}


async def get_video_info(video_path):
    try:
        if not ospath.exists(video_path):
            raise FileNotFoundError(f"File not found: {video_path}")
        clip = VideoFileClip(video_path)
        duration = clip.duration  # Duration in seconds
        clip.close()
        return duration
    except Exception as e:
        LOGS.error(f"Error in get_video_info: {e}")
        return None


def _sanitize_filename(name: str) -> str:
    # Replace problematic characters with underscore
    return re.sub(r'[\\/*?:"<>|\[\]]', '_', name)


class FFEncoder:
    """FFmpeg encoder with pause / resume / cancel and safer file handling.

    Important improvements over previous version:
    - Unique prog file per job (avoids races)
    - Unique temp input/output names per job (avoids collisions)
    - Ensures encode directory exists
    - Sanitizes output filename
    - Robust checks and cleanup on errors
    - Keeps inline control buttons visible during progress updates
    """

    def __init__(self, message, path, name, encodeid, qual):
        self.__proc = None
        self.is_cancelled = False
        self.is_paused = False
        self.message = message
        self.__name = name
        self.__qual = qual
        self.dl_path = path
        self.__total_time = None
        self.__encodeid = encodeid

        # ensure encode dir exists
        self.encode_dir = 'encode'
        os.makedirs(self.encode_dir, exist_ok=True)

        # sanitize the final output file name
        safe_name = _sanitize_filename(name)
        self.out_path = ospath.join(self.encode_dir, safe_name)

        # use per-job prog file to avoid races
        self.__prog_file = f'prog_{self.__encodeid}.txt'
        self.__start_time = time()

    async def progress(self):
        if isinstance(self.__total_time, str):
            self.__total_time = 1.0

        # create a markup that will be reused for every update so buttons remain visible
        control_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Pause", callback_data=f"pause_encoding:{self.__encodeid}"),
                InlineKeyboardButton("Resume", callback_data=f"resume_encoding:{self.__encodeid}"),
                InlineKeyboardButton("Cancel", callback_data=f"cancel_encoding:{self.__encodeid}")
            ]
        ])

        # loop while process exists and not cancelled
        while not (self.__proc is None or self.is_cancelled):
            # read progress file (may be empty initially)
            try:
                async with aiopen(self.__prog_file, 'r') as p:
                    text = await p.read()
            except FileNotFoundError:
                text = ''
            except Exception as e:
                LOGS.error(f"Error reading prog file {self.__prog_file}: {e}")
                text = ''

            if text:
                # parse progress fields safely
                t_matches = findall("out_time_ms=(\d+)", text)
                s_matches = findall(r"total_size=(\d+)", text)

                time_done = floor(int(t_matches[-1]) / 1000000) if t_matches else 1
                ensize = int(s_matches[-1]) if s_matches else 0

                diff = time() - self.__start_time
                speed = ensize / diff if diff > 0 else 0
                percent = round((time_done / max(self.__total_time or 1, 1)) * 100, 2)
                # protect against zero percent
                tsize = (ensize / (max(percent, 0.01) / 100)) if percent > 0 else ensize
                eta = (tsize - ensize) / max(speed, 0.01)

                bar = floor(percent / 8) * "█" + (12 - floor(percent / 8)) * "▒"

                progress_str = (
                    f"<blockquote>‣ <b>File Name :</b> <b><i>{self.__name}</i></b></blockquote>"
                    f"<blockquote>‣ <b>Status :</b> <i>{'Paused' if self.is_paused else 'Encoding'}</i>"
                    f"\n    <code>[{bar}]</code> {percent}%</blockquote> "
                    f"<blockquote>   ‣ <b>Size :</b> {convertBytes(ensize)} out of ~ {convertBytes(tsize)}"
                    f"\n    ‣ <b>Speed :</b> {convertBytes(speed)}/s"
                    f"\n    ‣ <b>Time Took :</b> {convertTime(diff)}"
                    f"\n    ‣ <b>Time Left :</b> {convertTime(eta)}</blockquote>"
                )

                try:
                    await editMessage(self.message, progress_str, reply_markup=control_markup)
                except Exception as e:
                    # ignore MessageNotModified and other transient errors
                    LOGS.debug(f"Ignored edit error for progress message: {e}")

                if (prog := findall(r"progress=(\w+)", text)) and prog[-1] == 'end':
                    break

            await asleep(6)

    async def start_encode(self):
        # ensure prog file is fresh
        try:
            if ospath.exists(self.__prog_file):
                await aioremove(self.__prog_file)
        except Exception:
            pass

        async with aiopen(self.__prog_file, 'w'):
            LOGS.info("Progress Temp Generated !")

        # get video duration
        self.__total_time = await get_video_info(self.dl_path)
        LOGS.info(f"Video duration: {self.__total_time} seconds")

        # create unique temp input/output names per job to avoid collisions
        dl_npath = ospath.join(self.encode_dir, f'ffanimeadvin_{self.__encodeid}.mkv')
        out_npath = ospath.join(self.encode_dir, f'ffanimeadvout_{self.__encodeid}.mkv')

        # check source exists before attempting rename
        if not ospath.exists(self.dl_path):
            raise FileNotFoundError(f"Source file missing: {self.dl_path}")

        try:
            # move to temp input path
            await aiorename(self.dl_path, dl_npath)
        except Exception as e:
            LOGS.error(f"Failed to move file to encode dir: {e}")
            # try a synchronous fallback copy via thread (safer on some environments)
            import shutil
            try:
                await to_thread(shutil.copy, self.dl_path, dl_npath)
                # try removing original
                try:
                    await to_thread(os.remove, self.dl_path)
                except Exception:
                    pass
            except Exception as e2:
                LOGS.error(f"Fallback copy also failed: {e2}")
                raise

        # build ffmpeg command from template
        ffcode = ffargs[self.__qual].format(dl_npath, self.__prog_file, out_npath)
        LOGS.info(f'FFCode: {ffcode}')

        # spawn process
        self.__proc = await create_subprocess_shell(ffcode, stdout=PIPE, stderr=PIPE)
        proc_pid = self.__proc.pid
        ffpids_cache.append(proc_pid)
        LOGS.info(f"Started encoding process with PID: {proc_pid}")

        # run progress reader and wait for process to finish
        try:
            _, return_code = await gather(create_task(self.progress()), self.__proc.wait())
        finally:
            # ensure pid removed
            try:
                ffpids_cache.remove(proc_pid)
            except Exception:
                pass

        # attempt to restore original filename back to downloads if desired
        try:
            # move original back if it still exists under dl_npath
            if ospath.exists(dl_npath):
                try:
                    await aiorename(dl_npath, self.dl_path)
                except Exception:
                    # if move back fails, leave it in place
                    LOGS.debug("Could not move temp input back to original path; leaving in encode dir")
        except Exception as e:
            LOGS.error(f"Error while restoring original file: {e}")

        if self.is_cancelled:
            # cleanup output temp if any
            try:
                if ospath.exists(out_npath):
                    await aioremove(out_npath)
            except Exception:
                pass
            return None

        if return_code == 0:
            if ospath.exists(out_npath):
                try:
                    await aiorename(out_npath, self.out_path)
                except Exception as e:
                    LOGS.error(f"Failed to move output to final path: {e}")
                    # fallback: try copy
                    import shutil
                    await to_thread(shutil.copy, out_npath, self.out_path)
                    await aioremove(out_npath)
                return self.out_path
            else:
                LOGS.error("Expected output not found after successful ffmpeg run")
                return None
        else:
            # capture stderr for debugging
            try:
                stderr = (await self.__proc.stderr.read()).decode().strip()
            except Exception:
                stderr = "(failed to read stderr)"
            await rep.report(stderr, "error")
            return None

    async def cancel_encode(self):
        self.is_cancelled = True
        if self.__proc is not None:
            try:
                self.__proc.kill()
                LOGS.info(f"Encoding cancelled for {self.__name}")
            except Exception as e:
                LOGS.error(f"Error cancelling process: {e}")

    async def pause_encode(self):
        """Pause encoding by sending SIGSTOP (Unix only)."""
        if self.__proc is not None and not self.is_paused:
            try:
                os.kill(self.__proc.pid, signal.SIGSTOP)
                self.is_paused = True
                LOGS.info(f"Paused encoding pid={self.__proc.pid}")
            except Exception as e:
                LOGS.error(f"Pause failed: {e}")
                raise

    async def resume_encode(self):
        """Resume encoding by sending SIGCONT (Unix only)."""
        if self.__proc is not None and self.is_paused:
            try:
                os.kill(self.__proc.pid, signal.SIGCONT)
                self.is_paused = False
                LOGS.info(f"Resumed encoding pid={self.__proc.pid}")
            except Exception as e:
                LOGS.error(f"Resume failed: {e}")
                raise
