import re
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader

from core import season as season_rules


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
TEMPLATES = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
# main.py와 같이 base.html이 쓰는 시즌 목록 등록
TEMPLATES.globals["season_context"] = season_rules.season_context


def scoreboard_back_url(query_params):
    html = TEMPLATES.get_template("scoreboard.html").render(
        request=SimpleNamespace(query_params=query_params),
        match_id=1,
    )
    match = re.search(
        r'<a\s+href="([^"]+)"\s+class="[^"]*sb-back',
        html,
    )
    assert match
    return match.group(1)


def test_scoreboard_returns_to_match_edit_list_when_requested():
    assert scoreboard_back_url({"return_to": "match-edit"}) == (
        "/settings?open=match-edit"
    )


def test_scoreboard_back_still_returns_home_for_normal_games():
    assert scoreboard_back_url({}) == "/"


def test_match_edit_list_marks_scoreboard_return_path():
    html = TEMPLATES.get_template("settings.html").render()

    assert "/scoreboard/${match.id}?return_to=match-edit" in html
    assert "get('open') === 'match-edit'" in html
    assert "showMatchEditModal();" in html
