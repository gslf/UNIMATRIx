from . import d3, d4, d5, d8
from .d1 import Information
from .d2 import Market
from .d3 import Coordination
from .d4 import Commons
from .d5 import Relationships
from .d6 import Institutions
from .d7 import Transmission
from .d8 import Adaptation
from .social import SocialWorld

RULE_TEXTS = [d3.ASSIGNMENT, d4.COVENANT, d4.REGENERATION, d5.RELEASE, d8.GRAMMAR, d8.SUGGESTION]

SCENARIOS = {
    c.domain: c
    for c in (
        Information,
        Market,
        Coordination,
        Commons,
        Relationships,
        Institutions,
        Transmission,
        Adaptation,
        SocialWorld,
    )
}


def get_scenario(domain):
    if domain not in SCENARIOS:
        raise ValueError("unknown_domain")
    return SCENARIOS[domain]()
