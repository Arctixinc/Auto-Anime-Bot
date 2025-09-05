import os
import time
from moviepy.editor import VideoFileClip
from PIL import Image
from asyncio import gather, create_task, sleep as asleep, Event
from asyncio.subprocess import PIPE
from os import path as ospath
from aiofiles import open as aiopen
from aiofiles.os import remove as aioremove
from traceback import format_exc
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery

from bot import bot, bot_loop, Var, ani_cache, ffQueue, ffLock, ff_queued
from .tordownload import TorDownloader
from .database import db
from .func_utils import getfeed, encode, editMessage, sendMessage, convertBytes
from .ffencoder import FFEncoder
from .tguploader import TgUploader
from .reporter import rep
from .utils import progress_for_pyrogram


btn_formatter = {
    '1080':'𝟭𝟬𝟴𝟬𝗽', 
    '720':'𝟳𝟮𝟬𝗽',
    '480':'𝟰𝟴𝟬𝗽',
    '360':'𝟯𝟲𝟬𝗽'
}

ff_encoders = {}
file_path_cache = {}


async def remove_queued_task(encodeid: int) -> bool:
    """Safely remove encodeid from ffQueue if present."""
    temp = []
    removed = False
    while not ffQueue.empty():
        task = await ffQueue.get()
        if task == encodeid:
            removed = True
            continue
        temp.append(task)
    for t in temp:
        await ffQueue.put(t)
    return removed


async def download_thumbnail(video, thumbnail_path="thumbnail.jpg"):
    try:
        clip = VideoFileClip(video)
        duration = clip.duration
        thumbnail_time = duration / 2
        frame = clip.get_frame(thumbnail_time)
        image = Image.fromarray(frame)
        image.save(thumbnail_path)
        clip.close()
        return thumbnail_path 
    except Exception as e:
        print(f"Error generating thumbnail: {e}")
        return None


def get_video_info(video_path):
    try:
        clip = VideoFileClip(video_path)
        duration = clip.duration
        width, height = clip.size
        clip.close()
        return duration, width, height
    except Exception as e:
        print(f"Error getting video info: {e}")
        return None, None, None
        

async def fetch_animes():
    await rep.report("Fetch Animes Started !!", "info")
    while True:
        await asleep(60)
        if ani_cache['fetch_animes']:
            for link in Var.RSS_ITEMS:
                if (info := await getfeed(link, 0)):
                    bot_loop.create_task(get_animes(info.title, info.link))


@bot.on_callback_query()
async def callback_handler(client, query: CallbackQuery):
    if query.data.startswith("queue_status:"):
        encodeid = int(query.data.split(":")[1])
        position = list(ffQueue._queue).index(encodeid) + 1
        total_tasks = ffQueue.qsize()
        await query.answer(
            f"Queue Position: {position}\nTotal Queue: {total_tasks}",
            show_alert=True
        )

    elif query.data.startswith("remove_task:"):
        encodeid = int(query.data.split(":")[1])
        fpath = file_path_cache.get(encodeid)

        removed = await remove_queued_task(encodeid)
        if removed:
            if fpath and os.path.exists(fpath):
                try:
                    await aioremove(fpath)
                    await query.answer("Task removed and file deleted.", show_alert=True)
                except Exception as e:
                    await query.answer(f"Error deleting file: {e}", show_alert=True)
            else:
                await query.answer("Task removed. File not found.", show_alert=True)

            file_path_cache.pop(encodeid, None)
            try:
                await query.message.delete()
            except:
                pass
        else:
            await query.answer("Task not found in queue.", show_alert=True)

    elif query.data.startswith("cancel_encoding:"):
        encodeid = int(query.data.split(":", 1)[1])
        encoder = ff_encoders.get(encodeid)
        if encoder:
            try:
                await encoder.cancel_encode()
            except Exception as e:
                await query.answer(f"Error cancelling: {e}", show_alert=True)
                return
            file_path_cache.pop(encodeid, None)
            await remove_queued_task(encodeid)
            ff_encoders.pop(encodeid, None)
            await query.answer("Encoding canceled.", show_alert=True)
            try:
                await query.message.delete()
            except:
                pass
            return
        await query.answer("No encoding task found.", show_alert=True)

    elif query.data.startswith("pause_encoding:"):
        encodeid = int(query.data.split(":", 1)[1])
        encoder = ff_encoders.get(encodeid)
        if encoder:
            try:
                await encoder.pause_encode()
                await query.answer("Encoding paused.", show_alert=True)
            except Exception as e:
                await query.answer(f"Failed to pause: {e}", show_alert=True)
        else:
            await query.answer("Task not found.", show_alert=True)

    elif query.data.startswith("resume_encoding:"):
        encodeid = int(query.data.split(":", 1)[1])
        encoder = ff_encoders.get(encodeid)
        if encoder:
            try:
                await encoder.resume_encode()
                await query.answer("Encoding resumed.", show_alert=True)
            except Exception as e:
                await query.answer(f"Failed to resume: {e}", show_alert=True)
        else:
            await query.answer("Task not found.", show_alert=True)


async def fencode(fname, fpath, message, m):
    encode = await m.edit_text(
        f"File downloaded successfully:\n\n"
        f"    • <b>File Name:</b> {fname}\n"
        f"    • <b>File Path:</b> {fpath}"
    )
    stat_msg = await m.edit_text(
        f"‣ <b>File Name :</b> <b><i>{fname}</i></b>\n\n<i>Processing...</i>",
    )
    
    encodeid = encode.id
    ffEvent = Event()
    ff_queued[encodeid] = ffEvent

    encoder = FFEncoder(stat_msg, fpath, fname, encodeid, "360")
    ff_encoders[encodeid] = encoder

    if ffLock.locked():
        file_path_cache[encodeid] = fpath
        queue_markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Queue Status", callback_data=f"queue_status:{encodeid}")],
                [InlineKeyboardButton("Remove from Queue", callback_data=f"remove_task:{encodeid}")],
                [InlineKeyboardButton("Cancel", callback_data=f"cancel_encoding:{encodeid}")]
            ]
        )
        await stat_msg.edit_text(
            f"‣ <b>File Name :</b> <b><i>{fname}</i></b>\n\n<i>Queued to Encode...</i>",
            reply_markup=queue_markup
        )

    await ffQueue.put(encodeid)
    await ffEvent.wait()

    if encoder.is_cancelled:   # ✅ fixed
        ff_encoders.pop(encodeid, None)
        file_path_cache.pop(encodeid, None)
        await stat_msg.edit_text("<b>Encoding canceled while in queue.</b>")
        return

    t = time.time()
    await ffLock.acquire()
    control_markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Pause", callback_data=f"pause_encoding:{encodeid}"),
                InlineKeyboardButton("Resume", callback_data=f"resume_encoding:{encodeid}"),
                InlineKeyboardButton("Cancel", callback_data=f"cancel_encoding:{encodeid}")
            ]
        ]
    )
    await stat_msg.edit_text(
        f"‣ <b>File Name :</b> <b><i>{fname}</i></b>\n\n<i>Ready to Encode...</i>",
        reply_markup=control_markup
    )

    out_path = None
    try:
        out_path = await encoder.start_encode()
    except Exception as e:
        if encoder.is_cancelled:   # ✅ fixed
            await stat_msg.edit_text("<b>Encoding canceled.</b>")
        else:
            await stat_msg.delete()
            if out_path and ospath.exists(out_path):
                await aioremove(out_path)
            if fpath and ospath.exists(fpath):
                await aioremove(fpath)
            ffLock.release()
            ff_encoders.pop(encodeid, None)
            return await message.reply(f"<b>Encoding failed: {str(e)}</b>")

    await stat_msg.edit_text("<b>Successfully Compressed. Now proceeding to upload...</b>")
    await asleep(1.5)

    try:
        start_time = time.time()
        duration, width, height = get_video_info(out_path)
        thumbnail_path = await download_thumbnail(out_path)
        msg = await bot.send_video(
            chat_id=message.chat.id,
            video=out_path,
            thumb=thumbnail_path,
            caption=f"‣ <b>File Name:</b> <i>{fname}</i>",
            duration=int(duration),
            width=width,
            height=height,
            supports_streaming=True,
            progress=progress_for_pyrogram,
            progress_args=("<b>Upload Started....</b>", stat_msg, start_time)
        )
        channel_ids = [
            int(-1001825550753),
            int(-1002373955828)
        ]
        for channel_id in channel_ids:
            await msg.copy(chat_id=channel_id)
    except Exception as e:
        await message.reply(f"<b>Error during upload: {e}</b>")
        await stat_msg.delete()
        if out_path and ospath.exists(out_path):
            await aioremove(out_path)
        if fpath and ospath.exists(fpath):
            await aioremove(fpath)
        ffLock.release()
        ff_encoders.pop(encodeid, None)
        return
    finally:
        if out_path and ospath.exists(out_path):
            await aioremove(out_path)
        if fpath and ospath.exists(fpath):
            await aioremove(fpath)
        if thumbnail_path and ospath.exists(thumbnail_path):
            await aioremove(thumbnail_path)

    ffLock.release()
    ff_encoders.pop(encodeid, None)
    file_path_cache.pop(encodeid, None)
    await stat_msg.delete()
    total_time = time.time() - t
    formatted_time = time.strftime("%H:%M:%S", time.gmtime(total_time))
    await message.reply(
        f"‣ <b>File Name:</b> <b><i>{fname}</i></b>\n\n"
        f"<i>Upload completed successfully.</i>\n"
        f"‣ <b>Total Time Taken:</b> {formatted_time}"
    )
