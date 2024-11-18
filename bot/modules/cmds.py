import os
import re
import time
from pyrogram import filters
from asyncio import sleep as asleep, gather
from pyrogram.filters import command, private, user, document, video
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import FloodWait, MessageNotModified
from bot import bot, bot_loop, Var, ani_cache
from bot.core.database import db
from bot.core.func_utils import decode, is_fsubbed, get_fsubs, editMessage, sendMessage, new_task, convertTime, getfeed
from bot.core.auto_animes import get_animes, fencode
from bot.core.reporter import rep
from bot.core.utils import progress_for_pyrogram

@bot.on_message(command('start') & private)
@new_task
async def start_msg(client, message):
    uid = message.from_user.id
    from_user = message.from_user
    txtargs = message.text.split()
    temp = await sendMessage(message, "<i>Connecting..</i>")
    if not await is_fsubbed(uid):
        txt, btns = await get_fsubs(uid, txtargs)
        return await editMessage(temp, txt, InlineKeyboardMarkup(btns))
    if len(txtargs) <= 1:
        await temp.delete()
        btns = []
        for elem in Var.START_BUTTONS.split():
            try:
                bt, link = elem.split('|', maxsplit=1)
            except:
                continue
            if len(btns) != 0 and len(btns[-1]) == 1:
                btns[-1].insert(1, InlineKeyboardButton(bt, url=link))
            else:
                btns.append([InlineKeyboardButton(bt, url=link)])
        smsg = Var.START_MSG.format(first_name=from_user.first_name,
                                    last_name=from_user.first_name,
                                    mention=from_user.mention, 
                                    user_id=from_user.id)
        if Var.START_PHOTO:
            await message.reply_photo(
                photo=Var.START_PHOTO, 
                caption=smsg,
                reply_markup=InlineKeyboardMarkup(btns) if len(btns) != 0 else None
            )
        else:
            await sendMessage(message, smsg, InlineKeyboardMarkup(btns) if len(btns) != 0 else None)
        return
    try:
        arg = (await decode(txtargs[1])).split('-')
    except Exception as e:
        await rep.report(f"User : {uid} | Error : {str(e)}", "error")
        await editMessage(temp, "<b>Input Link Code Decode Failed !</b>")
        return
    if len(arg) == 2 and arg[0] == 'get':
        try:
            fid = int(int(arg[1]) / abs(int(Var.FILE_STORE)))
        except Exception as e:
            await rep.report(f"User : {uid} | Error : {str(e)}", "error")
            await editMessage(temp, "<b>Input Link Code is Invalid !</b>")
            return
        try:
            msg = await client.get_messages(Var.FILE_STORE, message_ids=fid)
            if msg.empty:
                return await editMessage(temp, "<b>File Not Found !</b>")
            nmsg = await msg.copy(message.chat.id, reply_markup=None)
            await temp.delete()
            if Var.AUTO_DEL:
                async def auto_del(msg, timer):
                    await asleep(timer)
                    await msg.delete()
                await sendMessage(message, f'<i>File will be Auto Deleted in {convertTime(Var.DEL_TIMER)}, Forward to Saved Messages Now..</i>')
                bot_loop.create_task(auto_del(nmsg, Var.DEL_TIMER))
        except Exception as e:
            await rep.report(f"User : {uid} | Error : {str(e)}", "error")
            await editMessage(temp, "<b>File Not Found !</b>")
    else:
        await editMessage(temp, "<b>Input Link is Invalid for Usage !</b>")
    

@bot.on_message(command('log') & private & user(Var.ADMINS))
@new_task
async def _log(client, message):
    await message.reply_document("log.txt", quote=True)

@bot.on_message(command('link') & private & user(Var.ADMINS))
@new_task
async def _link(client, message):
    with open("/root/cfd.log") as f:
        url_match = re.search(r'https?://\S+\.trycloudflare\.com', f.read())
        await message.reply_text(url_match.group() if url_match else "No tunnel URL found.")
        
@bot.on_message(command('addtotask') & private & user(Var.ADMINS))
@new_task
async def add_to_task(client, message):    
    anime_name_msg = await client.ask(message.chat.id, "Please provide the anime name:")
    anime_name = anime_name_msg.text.strip()

    if not anime_name:
        return await sendMessage(message, "You must provide a valid anime name.")
    
    anime_link_msg = await client.ask(message.chat.id, "Please provide the magnet link:")
    anime_link = anime_link_msg.text.strip()

    if not anime_link:
        return await sendMessage(message, "You must provide a valid magnet link.")

    # Create the anime task with the provided name and link
    ani_task = bot_loop.create_task(get_animes(anime_name, anime_link, True))

    # Send a success message with the task details
    await sendMessage(message, f"<i><b>Task Added Successfully!</b></i>\n\n    • <b>Task Name:</b> {anime_name}\n    • <b>Task Link:</b> {anime_link}")
    

@bot.on_message((document | video) & private & user(Var.ADMINS))
@new_task
async def dwe_file(client, message):
    start_time = time.time()
    try:
        #m = await message.reply("File Received. Start Downloading.....")
        m = await message.reply(
            "<b>File Received. Start Downloading.....</b>",
            reply_to_message_id=message.id
        )
        # Download the file
        file_path = await client.download_media(
            message,
            progress=progress_for_pyrogram,
            progress_args=("<b>Download Started....</b>", m, start_time)
        )
    except Exception as e:
        return await message.reply(f"Failed to download the file: {str(e)}")

    if not file_path:
        return await message.reply("Failed to download the file. Please try again.")

    # Extract the file name
    file_name = (
        message.document.file_name if message.document else message.video.file_name
    )
    encode_task = bot_loop.create_task(fencode(file_name, file_path, message, m))



async def get_message_id(message):
    if message.forward_from_chat:
        return message.forward_from_message_id
    elif message.forward_sender_name:
        return 0
    elif message.text:
        pattern = r"https://t.me/(?:c/)?(.*)/(\d+)"
        matches = re.match(pattern, message.text)
        if not matches:
            return 0
        return int(matches.group(2))
    else:
        return 0


@bot.on_message(command("channel") & private & user(Var.ADMINS))
@new_task
async def channel_task(client, message):
    try:
        while True:
            try:
                first_message = await client.ask(
                    text="<b>Forward the First Message from the Channel</b>",
                    chat_id=message.from_user.id,
                    filters=(filters.forwarded | (filters.text & ~filters.forwarded)),
                    timeout=60,
                )
            except:
                return
            f_msg_id = await get_message_id(first_message)
            if f_msg_id:
                break
            else:
                await first_message.reply(
                    "<b>❌ Error\n\nThis forwarded post is not valid. Please forward a valid message from the channel.</b>",
                    quote=True,
                )

        # Get the second message
        while True:
            try:
                second_message = await client.ask(
                    text="<b>Forward the Second Message from the Channel</b>",
                    chat_id=message.from_user.id,
                    filters=(filters.forwarded | (filters.text & ~filters.forwarded)),
                    timeout=60,
                )
            except:
                return
            s_msg_id = await get_message_id(second_message)
            if s_msg_id:
                break
            else:
                await second_message.reply(
                    "<b>❌ Error\n\nThis forwarded post is not valid. Please forward a valid message from the channel.</b>",
                    quote=True,
                )

        # Ensure first_message_id < second_message_id
        start_msg_id = min(f_msg_id, s_msg_id)
        end_msg_id = max(f_msg_id, s_msg_id)
        chat_id = first_message.forward_from_chat.id

        await message.reply(
            f"<b>Processing messages from ID {start_msg_id} to {end_msg_id} in channel {chat_id}.</b>"
        )

        # Track the start time of the entire download process
        total_start_time = time.time()

        # Iterate through messages in the range
        for msg_id in range(start_msg_id, end_msg_id + 1):
            try:
                msg = await client.get_messages(chat_id, msg_id)
                if msg.video or (msg.document and msg.document.mime_type.startswith("video/")):
                    start_time = time.time()
                    reply_message = await message.reply(
                        f"<b>Downloading message {msg_id}...</b>"
                    )
                    file_path = await client.download_media(
                        msg,
                        progress=progress_for_pyrogram,
                        progress_args=(f"<b>Downloading...</b>", reply_message, start_time),
                    )
                    if file_path:
                        # Extract filename from message
                        file_name = (
                            msg.video.file_name if msg.video else msg.document.file_name
                        )

                        # Pass the downloaded file to video processing function
                        encode_task = bot_loop.create_task(fencode(file_name, file_path, message, reply_message))
                    else:
                        await reply_message.edit("Failed to download media.")
            except Exception as e:
                await message.reply(f"Error processing message {msg_id}: {str(e)}")

        # After all files have been processed
        total_time_taken = time.time() - total_start_time
        # Send the final message with time taken
        await message.reply(
            f"<b>✅ All files have been downloaded successfully!\n\nTotal time taken: {convertTime(total_time_taken)}</b>"
        )

    except Exception as e:
        await message.reply(f"An unexpected error occurred: {str(e)}")
