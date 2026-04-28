"""
Fetches skills from the internal GitHub repo at startup.
Skills are validated/registered by a separate pipeline — we just consume them.
Caches in Redis to avoid repeated fetches.
"""

import asyncio
import logging

import httpx
import redis.asyncio as aioredis

from config import Config

logger = logging.getLogger(__name__)

_redis: aioredis.Redis = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = await aioredis.from_url(Config.REDIS_URL, decode_responses=True)
    return _redis


async def fetch_skill(skill_name: str) -> str:
    """
    Fetch a skill from the GitHub Skills Repo.
    Falls back to empty string if unreachable — agent still works, just no skill hint.
    """
    cache_key = f"skill:{skill_name}"
    r = await get_redis()

    cached = await r.get(cache_key)
    if cached:
        logger.debug(f"Skill '{skill_name}' served from Redis cache")
        return cached

    filename = Config.SKILLS.get(skill_name)
    if not filename:
        logger.warning(f"No filename configured for skill '{skill_name}'")
        return ""

    url = f"{Config.SKILLS_REPO_BASE_URL}/{filename}"
    headers = {}
    if Config.SKILLS_GITHUB_TOKEN:
        headers["Authorization"] = f"token {Config.SKILLS_GITHUB_TOKEN}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            content = resp.text

        await r.set(cache_key, content, ex=Config.SKILLS_CACHE_TTL)
        logger.info(f"Skill '{skill_name}' fetched and cached ({len(content)} chars)")
        return content

    except Exception as e:
        logger.error(f"Failed to fetch skill '{skill_name}' from {url}: {e}")
        return ""


async def load_all_skills() -> dict[str, str]:
    """Load all configured skills. Returns dict of {skill_name: content}."""
    tasks = {name: fetch_skill(name) for name in Config.SKILLS}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    skills = {}
    for name, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            logger.error(f"Skill load error for '{name}': {result}")
            skills[name] = ""
        else:
            skills[name] = result
    return skills


async def invalidate_skill_cache(skill_name: str):
    """Call this when the validation pipeline pushes a new skill version."""
    r = await get_redis()
    await r.delete(f"skill:{skill_name}")
    logger.info(f"Cache invalidated for skill '{skill_name}'")
