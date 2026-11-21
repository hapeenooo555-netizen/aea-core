from io import BytesIO

from PIL import Image

from app.services.design_text_renderer import DesignCopy, render_design_copy


def test_renderer_uses_opaque_panel_and_sanitizes_placeholders_and_emoji():
    source = BytesIO()
    Image.new("RGB", (600, 750), (220, 220, 220)).save(source, format="PNG")

    rendered, width, height = render_design_copy(
        source.getvalue(),
        DesignCopy(
            title="Tangazo la AEA",
            body="Jifunze zaidi 🚀",
            cta="Jifunze zaidi [LINK_PLACEHOLDER] 🔗",
            brand="AEA Affiliate Hub",
        ),
    )

    assert (width, height) == (600, 750)
    with Image.open(BytesIO(rendered)) as result:
        assert result.format == "PNG"
        # The panel occupies the lower region and must be fully opaque,
        # preventing image-provider pseudo-text from showing through.
        panel_pixel = result.convert("RGB").getpixel((20, 500))
        assert panel_pixel == (8, 18, 35)


def test_renderer_falls_back_when_cta_is_only_a_placeholder():
    source = BytesIO()
    Image.new("RGB", (600, 750), (220, 220, 220)).save(source, format="PNG")

    rendered, _, _ = render_design_copy(
        source.getvalue(),
        DesignCopy(title="Tangazo la AEA", cta="[LINK_PLACEHOLDER]"),
    )

    assert rendered.startswith(b"\x89PNG")


def test_renderer_keeps_long_kiswahili_copy_inside_mobile_poster_canvas():
    source = BytesIO()
    Image.new("RGB", (1024, 1280), (15, 35, 90)).save(source, format="PNG")

    rendered, width, height = render_design_copy(
        source.getvalue(),
        DesignCopy(
            title="Mwongozo wa Ubunifu wa Poster ya AEA Affiliate Hub AI",
            body=(
                "Tengeneza maudhui bora kwa biashara yako. Panga kampeni zako, "
                "fikia wateja unaowalenga na tumia zana za kidijitali kwa urahisi."
            ),
            cta="Tembelea link kwenye bio kujifunza zaidi",
            brand="AEA Affiliate Hub AI",
        ),
    )

    assert (width, height) == (1024, 1280)
    with Image.open(BytesIO(rendered)) as result:
        assert result.format == "PNG"
        # The larger mobile-first copy panel is opaque and reaches into
        # the lower half of the portrait poster.
        assert result.convert("RGB").getpixel((60, 700)) == (5, 16, 34)


def test_renderer_cleans_placeholders_without_regex_warnings():
    import warnings

    source = BytesIO()
    Image.new("RGB", (600, 750), (220, 220, 220)).save(source, format="PNG")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", FutureWarning)
        rendered, _, _ = render_design_copy(
            source.getvalue(),
            DesignCopy(
                title="Tangazo la AEA",
                body="Jifunze zaidi [AUDIENCE_PLACEHOLDER] leo",
                cta="Tembelea [LINK_PLACEHOLDER]",
            ),
        )

    assert rendered.startswith(b"\\x89PNG")
    assert not [warning for warning in caught if issubclass(warning.category, FutureWarning)]
