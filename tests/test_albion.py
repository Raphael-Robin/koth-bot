from tournament_bot.albion import MAX_RESULTS, parse_search


def test_exact_match_comes_first_and_guild_is_optional():
    payload = {
        "players": [
            {"Id": "1", "Name": "Raphaelo", "GuildName": "Alpha"},
            {"Id": "2", "Name": "Raphael", "GuildName": ""},
            {"Id": "3", "Name": "Araphael", "GuildName": "Beta"},
        ]
    }
    result = parse_search(payload, "raphael")
    assert [c.id for c in result] == ["2", "3", "1"]
    assert result[0].guild is None


def test_results_are_capped_for_a_select_menu():
    payload = {"players": [{"Id": str(i), "Name": f"P{i}"} for i in range(40)]}
    assert len(parse_search(payload, "p")) == MAX_RESULTS


def test_empty_response():
    assert parse_search({}, "x") == []
