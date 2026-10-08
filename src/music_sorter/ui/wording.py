"""Presentation-only names; persisted provider, state and request values stay intact."""
from ..classification import LABELS

PROVIDERS = {'anthropic': 'Claude', 'openai': 'OpenAI', 'musicbrainz': 'MusicBrainz', 'lastfm': 'Last.fm'}
EXECUTIONS = {'sync': '바로 처리', 'batch': '나중에 결과 받기'}
SOURCES = {'manual': '직접 수정', 'llm': 'AI 분류', 'bulk': '여러 곡 수정', 'inherited': '기존 분류 이어받음'}
ACTIONS = {'manual': '직접 수정', 'unlock': '자동 수정 허용', 'llm': 'AI 분류', 'bulk': '여러 곡 수정',
           'restore_history': '이전 분류로 되돌림', 'accept_external_change': '변경된 파일 확인',
           'resolve_link_keep': '이전 기록 연결·분류 유지', 'resolve_link_reset': '이전 기록 연결·분류 초기화'}


def readable(message):
    value = str(message or '')
    for technical, plain in (
        ('USD 예산', '예산(미국 달러)'), ('미완료 예약', '처리 중인 요청의 예상 비용'),
        ('비용 예약', '처리 중인 요청의 예상 비용'), ('수동 보호', '직접 수정한 분류 보호'),
        ('원격 Batch', '서버에서 처리하는 작업'), ('Batch', '나중에 결과 받기'),
        ('입력 JSON', 'AI에 보낼 정보'), ('ID3', '파일에 저장된 음악 정보'),
        ('대분류-세부 장르 충돌', '대분류와 세부 장르가 맞지 않음'),
        ('정션/심볼릭 링크', '다른 폴더로 연결된 바로가기'),
    ):
        value = value.replace(technical, plain)
    return value


def classification_detail(inputs, result, reason):
    lines = ['AI에 보낼 곡 정보']
    names = {'title': '제목', 'artist': '아티스트', 'album': '앨범', 'version': '곡 버전',
             'existing_genre': '파일에 저장된 장르', 'filename': '파일 이름', 'year': '발매 연도', 'duration': '재생 시간(초)',
             'bitrate': '비트 전송률', 'sample_rate': '샘플링 주파수', 'vocal_hint': '보컬 참고 정보'}
    for key, label in names.items():
        if key in inputs:
            lines.append(f'{label}: {inputs[key] or "없음"}')
    lines.append('\n가사: ' + (inputs.get('lyrics') or '보내지 않음'))
    if inputs.get('protected'):
        lines.append('\n직접 수정한 분류 (자동 변경에서 보호)')
        for axis, field in inputs['protected'].items():
            value = field.get('value')
            lines.append(f'{LABELS.get(axis, axis)}: ' + ('확인 필요' if value is None else ' · '.join(value) if isinstance(value, list) else str(value)))
    external = inputs.get('external', [])
    if external:
        lines.append('\n외부 사이트에서 찾은 참고 정보')
        for item in external:
            lines.append(' · '.join(str(item.get(key, '')) for key in ('service', 'title', 'artist', 'album', 'tags') if item.get(key)))
            if item.get('match_reason'):
                lines.append('  연결 이유: ' + str(item['match_reason']))
    lines.append('\nAI 분류 결과')
    if result:
        for axis, field in result.get('classification', {}).items():
            value = field.get('value')
            label = '확인 필요' if value is None else '해당 없음' if value == [] else ' · '.join(value) if isinstance(value, list) else value
            lines.append(f'{LABELS.get(axis, axis)}: {label}')
            if field.get('reason'):
                lines.append('  이유: ' + readable(field['reason']))
        if result.get('suggested_tags'):
            lines.append('새 분류 이름 제안: ' + ' · '.join(result['suggested_tags']))
    else:
        lines.append('아직 분류 결과가 없습니다.')
    if reason:
        lines.append('\n안내: ' + readable(reason))
    return '\n'.join(lines)
