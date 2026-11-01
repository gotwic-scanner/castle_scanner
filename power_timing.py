"""Shared, versioned power lookup timing profiles (seconds)."""
PROFILES = {
    'original': {'init': 0.300, 'castle': 0.100, 'timeout': 4.000, 'water': 0.100, 'dismiss': 0.050},
    'recommended': {'init': 0.150, 'castle': 0.050, 'timeout': 2.500, 'water': 0.050, 'dismiss': 0.025},
    'aggressive': {'init': 0.100, 'castle': 0.025, 'timeout': 1.500, 'water': 0.025, 'dismiss': 0.010},
}
DEFAULT_PROFILE = 'recommended'

def timings(name):
    return PROFILES[name]
