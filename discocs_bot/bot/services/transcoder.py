import asyncio
import logging
import subprocess
from pathlib import Path

from bot.config import Settings
from bot.storage.models import Track

logger = logging.getLogger(__name__)


class TranscodeError(Exception):
    pass


def _run_subprocess(cmd: list[str], *, capture_stderr: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture_stderr else subprocess.DEVNULL,
        check=False,
    )


class Transcoder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        workers = max(1, settings.transcode_workers)
        self._semaphore = asyncio.Semaphore(workers)

    async def transcode_to_mp3(
        self,
        input_path: Path,
        output_path: Path,
        *,
        track: Track,
        cover_path: Path | None = None,
        bitrate: str | None = None,
    ) -> None:
        await self.transcode(
            input_path,
            output_path,
            track=track,
            cover_path=cover_path,
            audio_format="mp3",
            bitrate=bitrate or self._settings.transcode_bitrate,
        )

    async def transcode(
        self,
        input_path: Path,
        output_path: Path,
        *,
        track: Track,
        cover_path: Path | None = None,
        audio_format: str = "mp3",
        bitrate: str = "320k",
    ) -> None:
        if input_path.resolve() == output_path.resolve():
            raise TranscodeError("input and output paths must differ")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = self._build_transcode_command(
            input_path,
            output_path,
            track=track,
            cover_path=cover_path,
            audio_format=audio_format,
            bitrate=bitrate,
        )

        async with self._semaphore:
            input_mb = input_path.stat().st_size / (1024 * 1024) if input_path.exists() else 0
            logger.info(
                "Transcoding %s -> %s (format=%s, input=%.1f MB)",
                input_path.name,
                output_path.name,
                audio_format,
                input_mb,
            )
            result = await asyncio.to_thread(_run_subprocess, cmd)
            if result.returncode != 0:
                message = (result.stderr or b"").decode(errors="replace") or "ffmpeg failed"
                logger.error("ffmpeg failed: %s", message[-500:])
                raise TranscodeError(message)
            if output_path.exists():
                output_mb = output_path.stat().st_size / (1024 * 1024)
                logger.info("Transcode done %s (output=%.1f MB)", output_path.name, output_mb)

    def _build_transcode_command(
        self,
        input_path: Path,
        output_path: Path,
        *,
        track: Track,
        cover_path: Path | None,
        audio_format: str,
        bitrate: str,
    ) -> list[str]:
        cmd = ["ffmpeg", "-y", "-i", str(input_path)]
        embed_cover = self._should_embed_cover(cover_path, audio_format)
        if embed_cover and cover_path is not None:
            cmd.extend(["-i", str(cover_path)])
        cmd.extend(self._mapping_args(embed_cover))
        cmd.extend(self._codec_args(input_path, audio_format, bitrate, embed_cover))
        cmd.extend(self._metadata_args(track))
        cmd.append(str(output_path))
        return cmd

    @staticmethod
    def _should_embed_cover(cover_path: Path | None, audio_format: str) -> bool:
        return bool(cover_path is not None and cover_path.exists() and audio_format != "opus")

    @staticmethod
    def _mapping_args(embed_cover: bool) -> list[str]:
        args = ["-map", "0:a:0"]
        if embed_cover:
            args.extend(["-map", "1:v:0"])
        return args

    def _codec_args(
        self,
        input_path: Path,
        audio_format: str,
        bitrate: str,
        embed_cover: bool,
    ) -> list[str]:
        input_suffix = input_path.suffix.lower()
        if audio_format == "flac":
            return self._flac_codec_args(input_suffix, embed_cover)
        if audio_format == "opus":
            return ["-c:a", "libopus", "-b:a", bitrate, "-application", "audio"]
        if audio_format == "mp3" and input_suffix == ".mp3" and not embed_cover:
            return ["-c:a", "copy"]
        return self._mp3_codec_args(bitrate, embed_cover)

    @staticmethod
    def _flac_codec_args(input_suffix: str, embed_cover: bool) -> list[str]:
        args = ["-c:a", "copy"] if input_suffix == ".flac" and not embed_cover else [
            "-codec:a",
            "flac",
            "-compression_level",
            "5",
        ]
        if embed_cover:
            args.extend(["-c:v", "mjpeg", "-disposition:v", "attached_pic"])
        return args

    def _mp3_codec_args(self, bitrate: str, embed_cover: bool) -> list[str]:
        args = ["-codec:a", "libmp3lame", "-b:a", bitrate, "-threads", "0"]
        if self._settings.transcode_fast:
            args.extend(["-compression_level", "0"])
        if embed_cover:
            args.extend(["-c:v", "mjpeg", "-disposition:v", "attached_pic"])
        return args

    @staticmethod
    def _build_split_command(
        input_path: Path,
        output_path: Path,
        *,
        start_seconds: float,
        duration_seconds: float,
    ) -> list[str]:
        """Cut a window without re-encoding: mp3 frames copy losslessly."""
        return [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-t",
            f"{duration_seconds:.3f}",
            "-i",
            str(input_path),
            "-map",
            "0:a:0",
            "-c:a",
            "copy",
            str(output_path),
        ]

    async def split_audio(
        self,
        input_path: Path,
        output_path: Path,
        *,
        start_seconds: float,
        duration_seconds: float,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = self._build_split_command(
            input_path,
            output_path,
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
        )
        async with self._semaphore:
            result = await asyncio.to_thread(_run_subprocess, cmd)
        if result.returncode != 0 or not output_path.exists():
            message = (result.stderr or b"").decode(errors="replace") or "ffmpeg failed"
            logger.error("ffmpeg split failed: %s", message[-500:])
            raise TranscodeError(message)
        return output_path

    async def make_telegram_thumbnail(self, cover_path: Path, output_path: Path) -> Path | None:
        if not cover_path.exists():
            return None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(cover_path),
            "-vf",
            "scale=320:320:force_original_aspect_ratio=decrease",
            "-q:v",
            "4",
            str(output_path),
        ]
        result = await asyncio.to_thread(_run_subprocess, cmd, capture_stderr=False)
        if result.returncode != 0 or not output_path.exists():
            return None
        return output_path

    @staticmethod
    def _metadata_args(track: Track) -> list[str]:
        args: list[str] = []
        mapping = {
            "title": track.title,
            "artist": track.artist,
            "album": track.album,
        }
        if track.year:
            mapping["date"] = str(track.year)
        for key, value in mapping.items():
            if value:
                args.extend(["-metadata", f"{key}={value}"])
        return args

    @staticmethod
    def get_file_size(path: Path) -> int:
        return path.stat().st_size

    @staticmethod
    async def get_audio_duration(path: Path) -> int | None:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]

        def _probe() -> subprocess.CompletedProcess:
            return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)

        result = await asyncio.to_thread(_probe)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return int(float(result.stdout.decode().strip()))

    @staticmethod
    async def get_audio_bitrate_kbps(path: Path) -> int | None:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=bit_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]

        def _probe() -> subprocess.CompletedProcess:
            return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)

        result = await asyncio.to_thread(_probe)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            return max(1, int(int(result.stdout.decode().strip()) / 1000))
        except ValueError:
            return None
