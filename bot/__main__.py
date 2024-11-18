from asyncio import run as asyrun, sleep as asleep, all_tasks
from bot import bot, bot_loop, LOGS, ffQueue, ffLock, ff_queued
from bot.core.func_utils import clean_up

async def queue_loop():
    LOGS.info("Queue Loop Started !!")
    while True:
        if not ffQueue.empty():
            post_id = await ffQueue.get()
            await asleep(1.5)
            ff_queued[post_id].set()
            await asleep(1.5)
            async with ffLock:
                ffQueue.task_done()
        await asleep(10)

async def main():
    await bot.start()
    LOGS.info('Auto Anime Bot Started!')
    bot_loop.create_task(queue_loop())
    await bot.idle()
    LOGS.info('Auto Anime Bot Stopped!')
    await bot.stop()
    for task in all_tasks():
        task.cancel()
    await clean_up()
    LOGS.info('Finished AutoCleanUp !!')

if __name__ == '__main__':
    bot_loop.run_until_complete(main())
