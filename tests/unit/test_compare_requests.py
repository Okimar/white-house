import json

from tavern.devtools.compare_requests import compare, load, main, normalise, report, visible


def chat(*pairs, **params):
    return {"model": "m", "messages": [{"role": r, "content": t} for r, t in pairs], **params}


def kinds(diffs):
    return [d.kind for d in diffs]


def test_normalise_openai_claude_gemini_shapes():
    msgs, params = normalise(chat(("system", "S"), ("user", [{"type": "text", "text": "hi"}]), temperature=0.7))
    assert msgs == [("system", "S"), ("user", "hi")]
    assert params == {"model": "m", "temperature": 0.7}

    claude = {"system": [{"type": "text", "text": "S"}], "max_tokens": 5,
              "messages": [{"role": "user", "content": "hi"}]}
    assert normalise(claude) == ([("system", "S"), ("user", "hi")], {"max_tokens": 5})

    gemini = {"systemInstruction": {"parts": [{"text": "S"}]},
              "contents": [{"role": "user", "parts": [{"text": "hi"}]}, {"role": "model", "parts": [{"text": "yo"}]}],
              "generationConfig": {"temperature": 1, "stopSequences": ["x"]},
              "safetySettings": [{"category": "A", "threshold": "OFF"}]}
    msgs, params = normalise(gemini)
    assert msgs == [("system", "S"), ("user", "hi"), ("assistant", "yo")]
    assert params["generationConfig.temperature"] == 1
    assert params["generationConfig.stopSequences"] == ["x"]
    assert params["safetySettings"] == [{"category": "A", "threshold": "OFF"}]


def test_equal_requests_have_no_differences():
    a = chat(("system", "S"), ("user", "a"), ("user", "b"), temperature=1)
    assert compare(a, json.loads(json.dumps(a))) == []
    assert report([]) == "No differences."


def test_finds_role_text_and_param_differences():
    a = chat(("system", "Rules"), ("user", "Hello there"), temperature=0.7, stop=["\n"])
    b = chat(("user", "Rules"), ("user", "Hello  there"), temperature=0.9, max_tokens=100)
    diffs = compare(a, b)
    by_kind = {d.kind: d for d in diffs}
    assert "role" in by_kind and "system" in by_kind["role"].detail and "user" in by_kind["role"].detail
    text = [d for d in diffs if d.kind == "text"]
    assert len(text) == 1 and "-Hello there" in text[0].detail and "+Hello··there" in text[0].detail
    params = {d.where: d.detail for d in diffs if d.kind == "param"}
    assert set(params) == {"temperature", "stop", "max_tokens"}
    assert "0.7" in params["temperature"] and "0.9" in params["temperature"]
    assert "(missing)" in params["max_tokens"]


def test_message_count_and_missing_message():
    a = chat(("system", "S"), ("user", "one"), ("assistant", "two"), ("user", "three"))
    b = chat(("system", "S"), ("user", "one"), ("user", "three"))
    diffs = compare(a, b)
    assert kinds(diffs).count("count") == 1
    only = [d for d in diffs if d.kind == "only_a"]
    assert len(only) == 1 and "two" in only[0].detail


def test_merged_neighbours_with_same_content_are_named():
    a = chat(("system", "S"), ("user", "one"), ("user", "two"))
    b = chat(("system", "S"), ("user", "one\n\ntwo"))
    diffs = compare(a, b)
    assert "count" in kinds(diffs)
    assert [d for d in diffs if d.kind == "merged" and d.where.startswith("A:")]
    assert any("joined" in d.where for d in diffs if d.kind == "merged")
    assert "text" not in kinds(diffs) and "only_a" not in kinds(diffs)


def test_invisible_characters_are_spelled_out():
    assert visible("a⠀b") == "a<U+2800>b"
    assert visible("x​y") == "x<U+200B>y"
    assert visible("a b") == "a<U+00A0>b"
    assert visible("tab\there") == "tab<TAB>here"
    assert visible("end  ") == "end··"
    assert visible("  lead") == "··lead"
    diffs = compare(chat(("user", "a b")), chat(("user", "a⠀b")))
    assert any("<U+2800>" in d.detail for d in diffs if d.kind == "text")


def test_capture_files_and_cli(tmp_path, capsys):
    wrapper = {"timestamp": "t", "path": "/v1/chat/completions", "query": "", "headers": {},
               "body": chat(("user", "same"))}
    one, two = tmp_path / "a.json", tmp_path / "b.json"
    one.write_text(json.dumps(wrapper), encoding="utf-8")
    two.write_text(json.dumps(chat(("user", "same"))), encoding="utf-8")  # bare body
    assert load(one) == load(two)
    assert main([str(one), str(two)]) == 0
    assert "No differences" in capsys.readouterr().out
    two.write_text(json.dumps(chat(("user", "other"), temperature=2)), encoding="utf-8")
    assert main([str(one), str(two)]) == 1
    out = capsys.readouterr().out
    assert "[text]" in out and "[param]" in out and "temperature" in out
    assert main([str(one), str(tmp_path / "missing.json")]) == 2
