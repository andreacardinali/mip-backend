from prompting.template import PromptTemplate


def test_template_fingerprint_changes_with_contract_not_render_values():
    template = PromptTemplate(name="hello", content="Hello {name}")
    assert template.fingerprint == template.fingerprint
    assert template.render(name="A") != template.render(name="B")
    assert (
        template.fingerprint
        != PromptTemplate(name="hello", content="Hi {name}").fingerprint
    )
