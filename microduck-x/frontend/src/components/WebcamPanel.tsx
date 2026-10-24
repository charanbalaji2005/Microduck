import { useEffect, useRef, useState } from "react";
import { behaviorManager } from "../lib/autonomousBehavior";

interface WebcamPanelProps {
  open: boolean;
  onClose: () => void;
}

export default function WebcamPanel({ open, onClose }: WebcamPanelProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [trackingStatus, setTrackingStatus] = useState<string>("Initializing camera…");
  const [waveDetected, setWaveDetected] = useState(false);
  const [motionLevel, setMotionLevel] = useState(0);

  useEffect(() => {
    if (!open) {
      if (stream) {
        stream.getTracks().forEach((t) => t.stop());
        setStream(null);
      }
      return;
    }

    let active = true;
    navigator.mediaDevices
      .getUserMedia({ video: { width: 320, height: 240, facingMode: "user" }, audio: false })
      .then((s) => {
        if (!active) {
          s.getTracks().forEach((t) => t.stop());
          return;
        }
        setStream(s);
        setError(null);
        setTrackingStatus("Camera active · Looking for human presence…");
        if (videoRef.current) {
          videoRef.current.srcObject = s;
          videoRef.current.play().catch(() => {});
        }
      })
      .catch((err) => {
        if (!active) return;
        console.error("Camera access error:", err);
        setError(err.name === "NotAllowedError" ? "Camera permission denied" : "Camera unavailable");
        setTrackingStatus("Camera disconnected");
      });

    return () => {
      active = false;
      if (stream) {
        stream.getTracks().forEach((t) => t.stop());
      }
    };
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // Lightweight motion & wave detection
  useEffect(() => {
    if (!open || !stream) return;
    let raf = 0;
    const canvas = canvasRef.current || document.createElement("canvas");
    canvas.width = 32;
    canvas.height = 24;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    let prevData: Uint8ClampedArray | null = null;
    let waveStreak = 0;
    let lastWaveTrigger = 0;

    const detect = () => {
      raf = requestAnimationFrame(detect);
      const video = videoRef.current;
      if (!video || video.readyState < 2 || !ctx) return;

      ctx.drawImage(video, 0, 0, 32, 24);
      const img = ctx.getImageData(0, 0, 32, 24);
      const data = img.data;

      if (prevData) {
        let diffSum = 0;
        let leftMotion = 0;
        let rightMotion = 0;

        for (let i = 0; i < data.length; i += 4) {
          const l1 = (data[i] + data[i + 1] + data[i + 2]) / 3;
          const l0 = (prevData[i] + prevData[i + 1] + prevData[i + 2]) / 3;
          const d = Math.abs(l1 - l0);
          diffSum += d;
          const col = (i / 4) % 32;
          if (col < 16) leftMotion += d;
          else rightMotion += d;
        }

        const avgDiff = diffSum / (32 * 24);
        setMotionLevel(Math.min(100, Math.round(avgDiff * 3)));

        // Hand wave detection: strong localized oscillatory motion
        const isWaveMotion = avgDiff > 12 && Math.abs(leftMotion - rightMotion) > 300;
        if (isWaveMotion) {
          waveStreak++;
          if (waveStreak >= 3 && Date.now() - lastWaveTrigger > 6000) {
            lastWaveTrigger = Date.now();
            setWaveDetected(true);
            setTrackingStatus("Wave detected! 👋 Greeting Microduck…");
            behaviorManager.onWaveDetected();
            setTimeout(() => {
              setWaveDetected(false);
              setTrackingStatus("Presence detected · Tracking gestures…");
            }, 3500);
          }
        } else {
          waveStreak = Math.max(0, waveStreak - 1);
          if (Date.now() - lastWaveTrigger > 3500) {
            setTrackingStatus(avgDiff > 4 ? "Presence active · Listening for gestures" : "Observing · Wave at your duck!");
          }
        }
      }

      prevData = new Uint8ClampedArray(data);
    };

    raf = requestAnimationFrame(detect);
    return () => {
      cancelAnimationFrame(raf);
    };
  }, [open, stream]);

  if (!open) return null;

  return (
    <div className="webcam-modal">
      <div className="webcam-header">
        <div className="webcam-title">
          <span>📷 Human Interaction &amp; Vision</span>
          <span className={`webcam-status-pill ${waveDetected ? "wave" : stream ? "live" : "warn"}`}>
            {waveDetected ? "👋 Wave Detected!" : stream ? "Tracking Active" : "Disconnected"}
          </span>
        </div>
        <button className="webcam-close" onClick={onClose} title="Close Camera">×</button>
      </div>

      <div className="webcam-body">
        <div className="webcam-viewport">
          <video ref={videoRef} playsInline muted autoPlay className="webcam-video" />
          <canvas ref={canvasRef} style={{ display: "none" }} />
          {error && <div className="webcam-error">{error}</div>}
          <div className="webcam-hud">
            <span className="webcam-hud-text">{trackingStatus}</span>
            <div className="webcam-motion-bar" style={{ width: `${motionLevel}%` }} />
          </div>
        </div>

        <div className="webcam-info">
          <p>
            <strong>Privacy Guarantee:</strong> All computer vision runs 100% locally in your browser. No video is recorded or sent over the network.
          </p>
          <div className="webcam-tip">
            💡 <strong>Interactive Gesture:</strong> Wave your hand in front of the camera! Microduck will spot your wave, play a happy greeting chirp, and say hello!
          </div>
        </div>
      </div>
    </div>
  );
}
