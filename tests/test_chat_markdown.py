from app.services.chat_markdown import render_chat_markdown


def test_render_chat_markdown_common_blocks():
    markdown = "# 标题\n\n- 项目A\n- 项目B\n\n这是 **重点**，含 `code`。"
    html = render_chat_markdown(markdown)
    assert "<h1>标题</h1>" in html
    assert "<ul><li>项目A</li><li>项目B</li></ul>" in html
    assert "<strong>重点</strong>" in html
    assert "<code>code</code>" in html


def test_render_chat_markdown_escapes_html():
    markdown = "<script>alert('xss')</script>"
    html = render_chat_markdown(markdown)
    assert "<script>" not in html
    assert "&lt;script&gt;alert('xss')&lt;/script&gt;" in html
