"""Verify InputMediaPhoto for editMessageMedia includes attach:// media URI."""

from io import BytesIO

from telegram import InputFile, InputMediaPhoto
from telegram.request._requestparameter import RequestParameter


def test_edit_media_requires_attach_flag() -> None:
    payload = InputMediaPhoto(
        media=InputFile(BytesIO(b"fake-jpeg"), filename="cover.jpg", attach=True),
        caption="test",
    )
    param = RequestParameter.from_input("media", payload)

    assert isinstance(param.value, dict)
    assert param.value.get("media", "").startswith("attach://")
    assert param.multipart_data is not None
    assert len(param.multipart_data) == 1


def test_edit_media_without_attach_omits_media_field() -> None:
    payload = InputMediaPhoto(
        media=InputFile(BytesIO(b"fake-jpeg"), filename="cover.jpg"),
        caption="test",
    )
    param = RequestParameter.from_input("media", payload)

    assert isinstance(param.value, dict)
    assert "media" not in param.value


if __name__ == "__main__":
    test_edit_media_requires_attach_flag()
    test_edit_media_without_attach_omits_media_field()
    print("OK")
