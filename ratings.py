"""Equal-weight criterion ratings, with exact half-up rounding to 66."""
import json

CRITERIA = (('text', 'Текст'), ('beat', 'Бит'), ('flow', 'Флоу'),
            ('vibe', 'Вайб'), ('mix', 'Сведение / мастеринг'))


def calculate(scores, text_enabled=True):
    if type(text_enabled) is not bool:
        raise ValueError('Укажи, оценивается ли текст.')
    active = [key for key, _ in CRITERIA if key != 'text' or text_enabled]
    if any(type(scores.get(key)) is not int or not 1 <= scores[key] <= 6 for key in active):
        raise ValueError('Выбери оценку от 1 до 6 для каждого включённого критерия.')
    denominator = len(active) * 6
    return (66 * sum(scores[key] for key in active) + denominator // 2) // denominator


def details(row):
    try:
        value = json.loads(row.get('rating_details') or '{}')
        if not isinstance(value, dict) or value.get('version') != 1:
            return None
        calculate(value['scores'], value['text_enabled'])
        return value
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def score_label(row):
    return f"{row['rating']}/{row.get('rating_scale') or 10}" if row.get('rating') is not None else 'без оценки'


def summary(row):
    value = details(row)
    if not value:
        return ''
    return ' · '.join(f"{label}: {value['scores'][key]}/6" if key != 'text' or value['text_enabled']
                      else 'Текст: не оценивается' for key, label in CRITERIA)
