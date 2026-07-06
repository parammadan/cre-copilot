# CRE Copilot — Portal Agent VNC container (LOCAL demo only).
# A Linux box that runs headed Chromium on a virtual X display and streams it via noVNC, so the
# console can embed a TRUE live browser view (not screenshots). The backend drives this Chromium
# over CDP (port 9222); noVNC is served on 6080. Read-only navigation only; remediation never here.
FROM mcr.microsoft.com/playwright:v1.47.0-jammy

# noninteractive + a fixed TZ so tzdata (pulled in by x11vnc/novnc) never blocks on a prompt
ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC
RUN ln -fs /usr/share/zoneinfo/Etc/UTC /etc/localtime \
    && apt-get update && apt-get install -y --no-install-recommends \
        tzdata xvfb x11vnc novnc websockify socat \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV DISPLAY=:99
EXPOSE 6080 9222
ENTRYPOINT ["/entrypoint.sh"]
