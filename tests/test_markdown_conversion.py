from app.services.markdown_conversion import convert_pasted_text_to_markdown, convert_upload_to_markdown


def test_convert_text_upload_to_markdown():
    result = convert_upload_to_markdown("lesson.txt", "Hooke's law summary".encode("utf-8"))
    assert "# lesson" in result.lower()
    assert "Hooke's law summary" in result


def test_convert_pasted_text_to_markdown():
    result = convert_pasted_text_to_markdown("牛顿第二定律\nF=ma")
    assert "Pasted Text" in result
    assert "牛顿第二定律" in result
