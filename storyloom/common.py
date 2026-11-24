import manuscript_style
import story_additions
import premise_guard
import branch_edit
import branch_setup
from setting_edits import edit_source as setting_edit_source, heading_at as setting_heading_at, rename_request as setting_rename_request, requested_target as setting_requested_target, card_scope as setting_card_scope, materialized_facts as setting_materialized_facts
from character_identity import confirmed_character_aliases
import json
import re
import os
import sqlite3
import time
import uuid
import secrets
import difflib
import io
import zipfile
import threading
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from flask import Flask, jsonify, request, render_template, session, has_request_context, Response, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import HTTPException
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from dotenv import load_dotenv
from alternative_presentation import normalize_legacy_reference, normalize_legacy_plan
from backup_review import restore_review
from revision_tools import adjust_revision_length, apply_revision_patches, readable_manuscript, revision_timeline, repair_reviewed_manuscript, expansion_span, repeated_passage_issues, replays_existing_manuscript
from reference_quality import empty_reference_cards, fill_reference_descriptions, normalize_reference_topics, complete_reference_descriptions, normalize_character_layout, needs_reference_classification
from review_cache import reusable_findings
from reference_setup import REFERENCE_DESCRIPTION_STYLE, CHARACTER_ROLE_STYLE


from ai_client import GeminiUnavailable, RetryableAIError, gemini
from workflow import KINDS, DEFAULT_SETTINGS, parse_key, settings_value, apply_patches, compare_text, build_context, source_fingerprint, validate_ai_result, normalize_metadata, classify_document_updates
from workflow import CONTINUITY_CHECK, episode_continuity_stamp, normalize_manuscript_breaks
from workflow import linked_entity_renames, replace_linked_names, name_only_changes, branch_pending_start
from workflow import normalize_plan_heading
from workflow import episode_findings, is_plan_without_plot, is_concrete_place_name, REFERENCE_DOCUMENTS, reference_body, build_episode_context, CONTINUITY_RULES


def display_project_title(title):
    return re.sub(r'(?:\s*[(（]\s*가제\s*[)）])+\s*$', '', title).strip() or '제목 미정'
SETTING_FINDING_FIELDS = (
    '장면에서 한 행동이나 일시적인 다짐은 회차 사건(plot)에 기록하세요. '
    '지속되는 인물 목표나 관계만 등장인물에 기록하고, 능력의 발동 조건·효과·대가는 주요 설정의 기존 능력 이름으로 모으세요. '
    '인물에게 새 능력이 생기면 인물의 능력 항목에는 주요 설정 카드에 쓰는 이름만 적으세요. '
    '각 사실에 field를 붙이세요. 인물은 성격·목표·관계·능력, 세계관은 배경·규칙·제약, '
    '장소는 위치·특징, 주요 설정은 효과·사용 조건·대가·규칙 중 알맞은 하나를 선택하세요. 해당 없으면 빈 문자열로 두세요. '
    '같은 설정의 같은 속성을 여러 문장으로 되풀이하지 말고, 이미 기록된 정보와 비교하여 새로 밝혀진 내용만 간결하게 쓰세요. '
)


AI_MANUSCRIPT_LENGTH_MARGIN = 500


class AIUsageLimit(Exception):
    """App quota stopped dispatch, not a provider failure; keep checkpoints."""
    retryable = True

    def __init__(self, scope):
        self.scope = scope
        super().__init__('오늘 사용할 수 있는 AI 호출 횟수를 모두 사용했습니다. 내일 다시 이용해 주세요.'
                         if scope == 'user' else
                         '오늘 서비스의 AI 사용 한도에 도달했습니다. 내일 다시 이용해 주세요.')


def daily_limit(name, default):
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except (ValueError, TypeError):
        return default


