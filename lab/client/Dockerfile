# A stand-in LAN device (or, with a different command, the stand-in home router).
FROM alpine:3.22
RUN apk add --no-cache curl bind-tools iproute2 nftables ca-certificates openssl busybox-extras
COPY device.sh router.sh /
RUN chmod +x /device.sh /router.sh
CMD ["/device.sh"]
