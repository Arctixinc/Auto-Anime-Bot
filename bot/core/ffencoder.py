from re import findall 
from math import floor
from time import time
from os import path as ospath
from aiofiles import open as aiopen
from aiofiles.os import remove as aioremove, rename as aiorename
from shlex import split as ssplit
from asyncio import sleep as asleep, gather, create_subprocess_shell, create_task
from asyncio.subprocess import PIPE
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from moviepy.editor import VideoFileClip
from PIL import Image


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
        
import signal

class FFEncoder:
    def __init__(self, message, path, name, encodeid, qual):
        self.__proc = None
        self.is_cancelled = False
        self.is_paused = False   # ✅ NEW
        self.message = message
        self.__name = name
        self.__qual = qual
        self.dl_path = path
        self.__total_time = None
        self.out_path = ospath.join("encode", name)
        self.__prog_file = 'prog.txt'
        self.__start_time = time()
        self.__encodeid = encodeid

    async def progress(self):
        if isinstance(self.__total_time, str):
            self.__total_time = 1.0
        while not (self.__proc is None or self.is_cancelled):
            async with aiopen(self.__prog_file, 'r+') as p:
                text = await p.read()
            if text:
                time_done = floor(int(t[-1]) / 1000000) if (t := findall("out_time_ms=(\d+)", text)) else 1
                ensize = int(s[-1]) if (s := findall(r"total_size=(\d+)", text)) else 0

                diff = time() - self.__start_time
                speed = ensize / diff if diff > 0 else 0
                percent = round((time_done / max(self.__total_time, 1)) * 100, 2)
                tsize = ensize / (max(percent, 0.01) / 100)
                eta = (tsize - ensize) / max(speed, 0.01)

                bar = floor(percent / 8) * "█" + (12 - floor(percent / 8)) * "▒"

                progress_str = f"""<blockquote>‣ <b>File Name :</b> <b><i>{self.__name}</i></b></blockquote>
<blockquote>‣ <b>Status :</b> <i>{'Paused' if self.is_paused else 'Encoding'}</i>
    <code>[{bar}]</code> {percent}%</blockquote> 
<blockquote>   ‣ <b>Size :</b> {convertBytes(ensize)} out of ~ {convertBytes(tsize)}
    ‣ <b>Speed :</b> {convertBytes(speed)}/s
    ‣ <b>Time Took :</b> {convertTime(diff)}
    ‣ <b>Time Left :</b> {convertTime(eta)}</blockquote>"""

                await editMessage(self.message, progress_str)

                if (prog := findall(r"progress=(\w+)", text)) and prog[-1] == 'end':
                    break
            await asleep(8)

    async def start_encode(self):
        if ospath.exists(self.__prog_file):
            await aioremove(self.__prog_file)

        async with aiopen(self.__prog_file, 'w+'):
            LOGS.info("Progress Temp Generated !")

        self.__total_time = await get_video_info(self.dl_path)
        LOGS.info(f"Video duration: {self.__total_time} seconds")

        dl_npath, out_npath = ospath.join("encode", "ffanimeadvin.mkv"), ospath.join("encode", "ffanimeadvout.mkv")
        await aiorename(self.dl_path, dl_npath)

        ffcode = ffargs[self.__qual].format(dl_npath, self.__prog_file, out_npath)

        LOGS.info(f'FFCode: {ffcode}')
        self.__proc = await create_subprocess_shell(ffcode, stdout=PIPE, stderr=PIPE)
        proc_pid = self.__proc.pid
        ffpids_cache.append(proc_pid)
        LOGS.info(f"Started encoding process with PID: {proc_pid}")

        _, return_code = await gather(create_task(self.progress()), self.__proc.wait())
        ffpids_cache.remove(proc_pid)

        await aiorename(dl_npath, self.dl_path)

        if self.is_cancelled:
            return

        if return_code == 0:
            if ospath.exists(out_npath):
                await aiorename(out_npath, self.out_path)
            return self.out_path
        else:
            await rep.report((await self.__proc.stderr.read()).decode().strip(), "error")

    async def cancel_encode(self):
        self.is_cancelled = True
        if self.__proc is not None:
            try:
                self.__proc.kill()
            except:
                pass

    async def pause_encode(self):
        """Pause encoding by sending SIGSTOP"""
        if self.__proc is not None and not self.is_paused:
            try:
                os.kill(self.__proc.pid, 19)  # SIGSTOP
                self.is_paused = True
            except Exception as e:
                LOGS.error(f"Pause failed: {e}")
                raise

    async def resume_encode(self):
        """Resume encoding by sending SIGCONT"""
        if self.__proc is not None and self.is_paused:
            try:
                os.kill(self.__proc.pid, 18)  # SIGCONT
                self.is_paused = False
            except Exception as e:
                LOGS.error(f"Resume failed: {e}")
                raise
