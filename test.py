from onvif import ONVIFCamera
from datetime import datetime, timezone
import os

# 🌍 Forcer UTC côté Python
os.environ["TZ"] = "UTC"

IP = "10.21.9.21"
PORT = 80
USER = "leclerc"
PASSWORD = "LecOli%45"


def connect_camera():
    cam = ONVIFCamera(IP, PORT, USER, PASSWORD, no_cache=True)
    devicemgmt = cam.create_devicemgmt_service()
    media = cam.create_media_service()

    print("🔌 Connexion caméra OK")
    return devicemgmt, media


def check_time(devicemgmt):
    time_info = devicemgmt.GetSystemDateAndTime()

    dt = time_info.UTCDateTime

    camera_time = datetime(
        dt.Date.Year,
        dt.Date.Month,
        dt.Date.Day,
        dt.Time.Hour,
        dt.Time.Minute,
        dt.Time.Second,
        tzinfo=timezone.utc
    )

    server_time = datetime.now(timezone.utc)

    print("\n⏱ SYNCHRO TEMPS")
    print("📷 Caméra UTC :", camera_time)
    print("🖥 Serveur UTC :", server_time)

    diff = abs((server_time - camera_time).total_seconds())
    print(f"⏱ Différence : {diff:.2f} sec")

    if diff < 5:
        print("✅ Synchronisation OK")
    else:
        print("⚠️ Attention décalage")


def get_rtsp_url(media):
    print("\n🎥 Récupération flux RTSP...")

    profiles = media.GetProfiles()

    if not profiles:
        print("❌ Aucun profil vidéo trouvé")
        return None

    profile = profiles[0]

    stream_uri = media.GetStreamUri({
        'StreamSetup': {
            'Stream': 'RTP-Unicast',
            'Transport': {'Protocol': 'RTSP'}
        },
        'ProfileToken': profile.token
    })

    print("\n🎥 RTSP URL :")
    print(stream_uri.Uri)

    return stream_uri.Uri


def main():
    devicemgmt, media = connect_camera()

    # 1. test synchro temps
    check_time(devicemgmt)

    # 2. récupération RTSP
    rtsp_url = get_rtsp_url(media)

    if rtsp_url:
        print("\n🚀 Flux prêt à être utilisé !")
        print("👉 Utilise VLC ou OpenCV pour tester")


if __name__ == "__main__":
    main()