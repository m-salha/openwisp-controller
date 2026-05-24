from rest_framework.throttling import AnonRateThrottle


class AdoptionRateThrottle(AnonRateThrottle):
    """
    Limit unauthenticated adoption requests to 10 per minute per IP.
    Override via REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['adoption'] in settings.
    """
    scope = 'adoption'
    rate = '10/min'
