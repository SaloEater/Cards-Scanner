from __future__ import annotations

import cv2
import numpy as np

from app import config


def _order_points(pts: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]   # top-left
    rect[2] = pts[np.argmax(s)]   # bottom-right
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]  # top-right
    rect[3] = pts[np.argmax(diff)]  # bottom-left
    return rect


class CardDetector:
    def detect(self, bgr: np.ndarray) -> np.ndarray | None:
        h, w = bgr.shape[:2]
        min_area = h * w * config.MIN_CARD_AREA_FRACTION
        max_area = h * w * config.MAX_CARD_AREA_FRACTION

        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, config.CANNY_LOW, config.CANNY_HIGH)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=cv2.contourArea, reverse=True)

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > max_area:
                continue
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
            if len(approx) != 4:
                continue
            rect = _order_points(approx.reshape(4, 2))
            bw = np.linalg.norm(rect[1] - rect[0])
            bh = np.linalg.norm(rect[3] - rect[0])
            if bw == 0 or bh == 0:
                continue
            aspect = max(bw, bh) / min(bw, bh)
            if abs(aspect - config.CARD_ASPECT_TARGET) > config.CARD_ASPECT_TOLERANCE:
                continue
            return rect

        return None

    def crop_card(self, bgr: np.ndarray, bounds: np.ndarray | None) -> np.ndarray:
        out_w, out_h = config.CARD_OUTPUT_W, config.CARD_OUTPUT_H

        if bounds is not None:
            bw = np.linalg.norm(bounds[1] - bounds[0])
            bh = np.linalg.norm(bounds[3] - bounds[0])
            if bw > bh:
                out_w, out_h = out_h, out_w
            dst = np.float32([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]])
            m = cv2.getPerspectiveTransform(bounds.astype(np.float32), dst)
            return cv2.warpPerspective(bgr, m, (out_w, out_h))

        fh, fw = bgr.shape[:2]
        card_ratio = out_h / out_w  # 1.40
        if fw / fh > 1 / card_ratio:
            crop_h = fh
            crop_w = int(fh / card_ratio)
        else:
            crop_w = fw
            crop_h = int(fw * card_ratio)
        x0 = (fw - crop_w) // 2
        y0 = (fh - crop_h) // 2
        center = bgr[y0:y0 + crop_h, x0:x0 + crop_w]
        return cv2.resize(center, (out_w, out_h))

    def detect_debug(
        self, bgr: np.ndarray
    ) -> tuple[np.ndarray | None, np.ndarray, np.ndarray, str]:
        """Returns (bounds, edges_bgr, contours_bgr, rejection_reason)."""
        h, w = bgr.shape[:2]
        min_area = h * w * config.MIN_CARD_AREA_FRACTION
        max_area = h * w * config.MAX_CARD_AREA_FRACTION

        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, config.CANNY_LOW, config.CANNY_HIGH)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        edges_bgr = cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)

        contours_bgr = bgr.copy()
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=cv2.contourArea, reverse=True)

        if not contours:
            return None, edges_bgr, contours_bgr, "No contours found"

        best_rejection = "All contours fail area check"
        bounds = None

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > max_area:
                cv2.drawContours(contours_bgr, [cnt], -1, (80, 80, 80), 1)
                continue

            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
            if len(approx) != 4:
                cv2.drawContours(contours_bgr, [cnt], -1, (255, 100, 0), 2)
                cx, cy = map(int, approx.reshape(-1, 2).mean(axis=0))
                cv2.putText(contours_bgr, f"{len(approx)}v", (cx, cy),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 100, 0), 1)
                if best_rejection == "All contours fail area check":
                    best_rejection = f"Best candidate: {len(approx)} corners (need 4)"
                continue

            rect = _order_points(approx.reshape(4, 2))
            bw = np.linalg.norm(rect[1] - rect[0])
            bh = np.linalg.norm(rect[3] - rect[0])
            if bw == 0 or bh == 0:
                continue
            aspect = max(bw, bh) / min(bw, bh)

            if abs(aspect - config.CARD_ASPECT_TARGET) > config.CARD_ASPECT_TOLERANCE:
                pts = rect.astype(np.int32)
                cv2.polylines(contours_bgr, [pts], True, (0, 220, 220), 2)
                cx, cy = pts.mean(axis=0).astype(int)
                cv2.putText(contours_bgr, f"{aspect:.2f}", (cx - 20, cy),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 220), 2)
                if "corners" not in best_rejection and best_rejection != "":
                    best_rejection = (
                        f"Best: aspect {aspect:.2f} "
                        f"(target {config.CARD_ASPECT_TARGET:.2f} "
                        f"±{config.CARD_ASPECT_TOLERANCE:.2f})"
                    )
                continue

            # Winner
            bounds = rect
            pts = rect.astype(np.int32)
            cv2.polylines(contours_bgr, [pts], True, (0, 255, 0), 3)
            for pt in pts:
                cv2.circle(contours_bgr, tuple(pt), 8, (0, 255, 0), -1)
            best_rejection = ""
            break

        return bounds, edges_bgr, contours_bgr, best_rejection

    def draw_overlay(self, bgr: np.ndarray, bounds: np.ndarray | None) -> np.ndarray:
        if bounds is not None:
            pts = bounds.astype(np.int32)
            cv2.polylines(bgr, [pts], True, (0, 255, 0), 3)
            for pt in pts:
                cv2.circle(bgr, tuple(pt), 8, (0, 255, 0), -1)
        else:
            fh, fw = bgr.shape[:2]
            out_w, out_h = config.CARD_OUTPUT_W, config.CARD_OUTPUT_H
            card_ratio = out_h / out_w
            if fw / fh > 1 / card_ratio:
                crop_h, crop_w = fh, int(fh / card_ratio)
            else:
                crop_w, crop_h = fw, int(fw * card_ratio)
            x0, y0 = (fw - crop_w) // 2, (fh - crop_h) // 2
            cv2.rectangle(bgr, (x0, y0), (x0 + crop_w, y0 + crop_h), (0, 165, 255), 2)
        return bgr
