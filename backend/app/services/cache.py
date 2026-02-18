import json
from typing import Dict, Any
import logging

from app.core.redis_client import redis_client as app_redis

logger = logging.getLogger(__name__)

# Simple in-memory fallback cache when Redis is not available
_memory_cache: Dict[str, str] = {}

async def get_revenue_summary(property_id: str, tenant_id: str) -> Dict[str, Any]:
    """
    Fetches revenue summary, utilizing caching to improve performance.
    Falls back to in-memory cache when Redis is unavailable.
    """
    cache_key = f"revenue:{tenant_id}:{property_id}"
    
    # Try to get from cache (Redis or in-memory fallback)
    try:
        if app_redis.is_connected:
            cached = await app_redis.get(cache_key)
            if cached:
                return json.loads(cached)
        elif cache_key in _memory_cache:
            return json.loads(_memory_cache[cache_key])
    except Exception as e:
        logger.warning(f"Cache read failed for {cache_key}: {e}")
    
    # Revenue calculation is delegated to the reservation service.
    from app.services.reservations import calculate_total_revenue
    
    # Calculate revenue
    result = await calculate_total_revenue(property_id, tenant_id)
    
    # Cache the result for 5 minutes
    try:
        result_json = json.dumps(result)
        if app_redis.is_connected:
            await app_redis.setex(cache_key, 300, result_json)
        else:
            _memory_cache[cache_key] = result_json
    except Exception as e:
        logger.warning(f"Cache write failed for {cache_key}: {e}")
    
    return result
