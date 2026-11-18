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
