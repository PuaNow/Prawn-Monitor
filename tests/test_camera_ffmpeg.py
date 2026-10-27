import pytest

from prawn_monitor.camera import build_camera, ffmpeg_camera_command


def test_v4l2_and_ip_commands_scale_and_do_not_force_rtsp_on_http():
    v4l2 = ffmpeg_camera_command(
        {
            "driver": "v4l2",
            "device": "/dev/video0",
            "width": 1280,
            "height": 720,
            "fps": 15,
            "input_format": "mjpeg",
        }
    )
    assert "v4l2" in v4l2
    assert "/dev/video0" in v4l2
    assert "mjpeg" in v4l2
    assert "scale=1280:720,format=rgb24" in v4l2
    index = ffmpeg_camera_command({"driver": "v4l2", "device": 2, "width": 640, "height": 480, "fps": 10})
    assert "/dev/video2" in index

    rtsp = ffmpeg_camera_command(
        {"driver": "ip", "url": "rtsp://192.168.1.50/stream", "width": 640, "height": 480, "fps": 10}
    )
    assert "-rtsp_transport" in rtsp
    assert "tcp" in rtsp
    http = ffmpeg_camera_command(
        {"driver": "ip", "url": "http://192.168.1.50/video.mjpg", "width": 640, "height": 480, "fps": 10}
    )
    assert "rtsp_transport" not in http
    assert "http://192.168.1.50/video.mjpg" in http


def test_build_camera_does_not_open_the_device_until_a_frame():
    camera = build_camera(
        {"camera": {"driver": "v4l2", "device": "/dev/video0", "width": 640, "height": 480}},
        "Nursery",
    )
    assert camera.driver_name == "v4l2"
    assert camera._proc is None
    described = camera.describe()
    assert described["auto_exposure"] is None
    assert "AMADEUS" in described["note"]
    camera.close()

    ip = build_camera({"camera": {"driver": "ip", "width": 640, "height": 480}}, "Nursery")
    with pytest.raises(ValueError, match="url"):
        ip.frame_rgb()
    ip.close()

    with pytest.raises(ValueError, match="even"):
        build_camera({"camera": {"driver": "v4l2", "width": 641, "height": 480}}, "Nursery")
