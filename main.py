from __future__ import annotations
import os, io, json, time, asyncio, logging, random
from datetime import datetime, timedelta
from typing import Any, Dict

# keep-alive web server
import keep_alive
keep_alive.start()

# Discord & I/O
import discord, aiohttp, numpy as np
from discord.ext import commands
from dotenv import load_dotenv
from PIL import Image, ImageFilter

# Config & Logging
load_dotenv()
TOKEN       = os.getenv("DISCORD_TOKEN")
SCORES_FILE = "scores.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("ind-bot")

# Photo prompts for ind.daily
PHOTO_PROMPTS = [
    "a close-up of colorful autumn leaves",
    "reflections in a still pond",
    "a portrait shot in natural window light",
    "symmetry in architectural structures",
    "a city skyline at golden hour",
    "interesting shadow and light patterns",
    "street photography capturing candid moments",
    "a macro shot of a vibrant flower",
    "a silhouette against a sunset sky",
    "abstract textures and patterns"
]

# Persistence
def load_scores() -> Dict[str, Any]:
    if os.path.exists(SCORES_FILE):
        return json.load(open(SCORES_FILE, "r", encoding="utf-8"))
    return {"images": [], "users": {}}

def save_scores(db: Dict[str, Any]) -> None:
    json.dump(db, open(SCORES_FILE, "w", encoding="utf-8"), indent=2)

# Composition heuristic (rule-of-thirds)
def composition_score(img: Image.Image) -> float:
    edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
    arr   = np.array(edges, dtype=np.float32) / 255.0
    h, w  = arr.shape
    ys, xs = np.indices((h, w))
    total = arr.sum()
    if total == 0:
        return 5.0
    cx = (arr * xs).sum() / total
    cy = (arr * ys).sum() / total
    pts = [(w/3, h/3), (w/3, 2*h/3), (2*w/3, h/3), (2*w/3, 2*h/3)]
    dists = [np.hypot(cx - x0, cy - y0) for x0, y0 in pts]
    dmin = min(dists)
    maxd = np.hypot(w/3, h/3)
    return float((1 - min(dmin / maxd, 1.0)) * 10.0)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members         = True
bot = commands.Bot(command_prefix="ind.", intents=intents, help_command=None)

def extract_image_url(msg: discord.Message) -> str | None:
    for a in msg.attachments:
        if a.content_type and a.content_type.startswith("image"):
            return a.url
    for e in msg.embeds:
        if e.image and e.image.url:
            return e.image.url
    return None

async def score_image(url: str) -> float:
    async with aiohttp.ClientSession() as sess:
        async with sess.get(url) as resp:
            raw = await resp.read()
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    return composition_score(img)

# Events
@bot.event
async def on_ready():
    logger.info("Logged in as %s", bot.user)

@bot.event
async def on_message(msg: discord.Message):
    if msg.author.bot:
        return
    await bot.process_commands(msg)
    url = extract_image_url(msg)
    if not url:
        return
    await msg.reply("🔍 Scoring your image...")
    try:
        async with msg.channel.typing():
            score = await score_image(url)
    except Exception as e:
        await msg.reply(f"⚠️ Scoring error: {e}")
        return
    db  = load_scores()
    uid = str(msg.author.id)
    db["images"].append({
        "user": uid,
        "score": score,
        "ts": datetime.utcnow().isoformat()
    })
    rec = db["users"].setdefault(uid, {"scores": [], "dates": []})
    rec["scores"].append(score)
    today = datetime.utcnow().date().isoformat()
    if today not in rec["dates"]:
        rec["dates"].append(today)
    save_scores(db)
    await msg.reply(f"🖼️ **Image score:** {score:.2f}/10")

# Commands
@bot.command(name="help")
async def ind_help(ctx):
    lines = [
        "ind.help   – Lists all available IND commands.",
        "ind.avg    – Shows your lifetime average score.",
        "ind.rank   – Tells you your all-time rank and average.",
        "ind.week   – Displays the Top 5 averages from the last 7 days.",
        "ind.streak – Shows how many consecutive days you’ve posted a photo.",
        "ind.top    – Shows the Top 5 all-time averages.",
        "ind.daily  – Gives a random photo theme prompt.",
        "ind.reset  – `@user` (admin only) Wipes a user’s scores and streak."
    ]
    await ctx.reply("**IND Bot Commands**\n" + "\n".join(lines))

@bot.command(name="avg")
async def ind_avg(ctx):
    s = load_scores()["users"].get(str(ctx.author.id))
    if not s or not s["scores"]:
        return await ctx.reply("You have no scores yet.")
    avg = sum(s["scores"]) / len(s["scores"])
    await ctx.reply(f"Your average composition: **{avg:.2f}/10**")

@bot.command(name="top")
async def ind_top(ctx):
    users = load_scores()["users"]
    board = sorted(
        ((uid, sum(u["scores"]) / len(u["scores"]))
         for uid,u in users.items() if u["scores"]),
        key=lambda x: x[1], reverse=True
    )[:5]
    if not board:
        return await ctx.reply("No scores yet.")
    lines = []
    for i,(uid,avg) in enumerate(board,1):
        m = ctx.guild.get_member(int(uid)) if ctx.guild else None
        name = m.display_name if m else f"User {uid}"
        lines.append(f"`#{i}` **{name}** – {avg:.2f}/10")
    await ctx.reply("🏆 **Top 5 Averages**\n" + "\n".join(lines))

@bot.command(name="rank")
async def ind_rank(ctx):
    users = load_scores()["users"]
    board = sorted(
        ((uid, sum(u["scores"]) / len(u["scores"]))
         for uid,u in users.items() if u["scores"]),
        key=lambda x: x[1], reverse=True
    )
    uids = [uid for uid,_ in board]
    me = str(ctx.author.id)
    if me not in uids:
        return await ctx.reply("You’re not ranked yet. Post an image!")
    pos = uids.index(me) + 1
    avg = dict(board)[me]
    await ctx.reply(f"You are **#{pos}** with an average of {avg:.2f}/10.")

@bot.command(name="week")
async def ind_week(ctx):
    cutoff = datetime.utcnow() - timedelta(days=7)
    images = load_scores()["images"]
    recent_scores: Dict[str, list[float]] = {}
    for img in images:
        ts = datetime.fromisoformat(img["ts"])
        if ts > cutoff:
            recent_scores.setdefault(img["user"], []).append(img["score"])
    board = sorted(
        ((uid, sum(scores)/len(scores))
         for uid,scores in recent_scores.items()),
        key=lambda x: x[1], reverse=True
    )[:5]
    if not board:
        return await ctx.reply("No activity in the last 7 days.")
    lines = []
    for i,(uid,avg) in enumerate(board,1):
        m = ctx.guild.get_member(int(uid)) if ctx.guild else None
        name = m.display_name if m else f"User {uid}"
        lines.append(f"`#{i}` **{name}** – {avg:.2f}/10")
    await ctx.reply("📅 **Weekly Top 5**\n" + "\n".join(lines))

@bot.command(name="streak")
async def ind_streak(ctx):
    data = load_scores()["users"].get(str(ctx.author.id), {})
    dates = sorted(data.get("dates", []), reverse=True)
    streak = 0
    today = datetime.utcnow().date()
    for d in dates:
        if datetime.fromisoformat(d).date() == today:
            streak += 1
            today -= timedelta(days=1)
        else:
            break
    if streak:
        await ctx.reply(f"🔥 Your posting streak: **{streak}** day(s)!")
    else:
        await ctx.reply("No current streak. Post an image today to start one!")

@bot.command(name="daily")
async def ind_daily(ctx):
    prompt = random.choice(PHOTO_PROMPTS)
    await ctx.reply(f"📸 **Today's photo theme:** {prompt}")

@bot.command(name="reset")
@commands.has_permissions(administrator=True)
async def ind_reset(ctx, member: discord.Member):
    uid = str(member.id)
    db = load_scores()
    if uid in db["users"]:
        del db["users"][uid]
        db["images"] = [img for img in db["images"] if img["user"] != uid]
        save_scores(db)
        await ctx.reply(f"✅ Cleared scores for **{member.display_name}**.")
    else:
        await ctx.reply(f"No data found for {member.display_name}.")

# Run
if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN missing")
    bot.run(TOKEN)
