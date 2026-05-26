import logging
from enum import Enum, auto

# Define the logger for this module
logger = logging.getLogger(__name__)

class Gender(Enum):
    MALE = auto()
    FEMALE = auto()

class VoiceConfig:
    _CONFIG_MAP = {
        "achernar": Gender.FEMALE,
        "achird": Gender.MALE,
        "algenib": Gender.MALE,
        "algieba": Gender.MALE,
        "alnilam": Gender.MALE,
        "aoede": Gender.FEMALE,
        "autonoe": Gender.FEMALE,
        "callirrhoe": Gender.FEMALE,
        "charon": Gender.MALE,
        "despina": Gender.FEMALE,
        "enceladus": Gender.MALE,
        "erinome": Gender.FEMALE,
        "fenrir": Gender.MALE,
        "gacrux": Gender.FEMALE,
        "iapetus": Gender.MALE,
        "kore": Gender.FEMALE,
        "laomedeia": Gender.FEMALE,
        "leda": Gender.FEMALE,
        "orus": Gender.MALE,
        "pulcherrima": Gender.FEMALE,
        "puck": Gender.MALE,
        "rasalgethi": Gender.MALE,
        "sadachbia": Gender.MALE,
        "sadaltager": Gender.MALE,
        "schedar": Gender.MALE,
        "sulafat": Gender.FEMALE,
        "umbriel": Gender.MALE,
        "vindemiatrix": Gender.FEMALE,
        "zephyr": Gender.FEMALE,
        "zubenelgenubi": Gender.MALE,
    }

    @classmethod
    def get_gender(cls, value: str) -> Gender:
        """
        Takes a string value and returns the corresponding Gender ENUM.
        Logs an error and defaults to MALE if the value is not recognized.
        """
        normalized_value = value.lower().strip()
        
        if normalized_value not in cls._CONFIG_MAP:
            # Log the error with the original value for easier debugging
            logger.error("Value '%s' is not present in the configuration mapping. Defaulting to MALE.", value)
            return Gender.MALE
            
        return cls._CONFIG_MAP[normalized_value]

    @classmethod
    def is_female(cls, value: str) -> bool:
        """
        Returns True if the value maps to Gender.FEMALE, otherwise False.
        """
        return cls.get_gender(value) == Gender.FEMALE