// Read-only newest scout_reports document. This route never writes.

import { cert, getApps, initializeApp } from "firebase-admin/app";
import { getFirestore } from "firebase-admin/firestore";

function unconfigured() {
  const error = new Error("Firestore reader is not configured.");
  error.code = "firestore_unconfigured";
  return error;
}

function textOrNull(value) {
  if (typeof value !== "string") return null;
  const text = value.trim();
  return text || null;
}

function asIso(value) {
  if (value == null || value === "") return null;
  if (typeof value === "string") return value.trim() || null;
  if (typeof value.toDate === "function") {
    try {
      return value.toDate().toISOString();
    } catch {
      return null;
    }
  }
  if (value instanceof Date) return value.toISOString();
  return null;
}

function parseStructured(value) {
  if (value == null) return null;
  if (typeof value === "string") {
    const text = value.trim();
    if (!text) return null;
    try {
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
      return parsed;
    } catch {
      return null;
    }
  }
  if (typeof value === "object" && !Array.isArray(value)) return value;
  return null;
}

function extractPicks(structured) {
  if (!structured) return [];
  return Array.isArray(structured.picks) ? structured.picks : [];
}

function getAdminFirestore() {
  const raw = process.env.SCOUT_FIRESTORE_SERVICE_ACCOUNT_JSON;
  const projectId = process.env.SCOUT_FIRESTORE_PROJECT_ID;
  if (!raw || !String(raw).trim() || !projectId || !String(projectId).trim()) {
    throw unconfigured();
  }

  let credentials;
  try {
    credentials = JSON.parse(String(raw));
  } catch {
    throw unconfigured();
  }
  if (!credentials || typeof credentials !== "object" || Array.isArray(credentials)) {
    throw unconfigured();
  }

  if (!getApps().length) {
    initializeApp({
      credential: cert(credentials),
      projectId: String(projectId).trim(),
    });
  }

  return getFirestore();
}

export default async function handler(req, res) {
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET");
    res.status(405).json({ ok: false, error: "method_not_allowed" });
    return;
  }

  try {
    const db = getAdminFirestore();
    const snapshot = await db.collection("scout_reports").orderBy("generated_at", "desc").limit(1).get();

    if (snapshot.empty) {
      res.status(404).json({
        ok: false,
        error: "no_report",
        message: "No scout_reports document found.",
      });
      return;
    }

    const doc = snapshot.docs[0];
    const data = doc.data() || {};
    const structured = parseStructured(
      data.structured_report_json != null ? data.structured_report_json : data.structuredReportJson
    );

    res.status(200).json({
      ok: true,
      report: {
        report_id: textOrNull(data.report_id) || textOrNull(data.reportId) || doc.id,
        market_date: textOrNull(data.market_date) || textOrNull(data.marketDate),
        generated_at: asIso(data.generated_at != null ? data.generated_at : data.generatedAt),
        picks: extractPicks(structured),
      },
    });
  } catch (error) {
    if (error && error.code === "firestore_unconfigured") {
      res.status(503).json({
        ok: false,
        error: "firestore_unconfigured",
        message: "Firestore reader is not configured.",
      });
      return;
    }
    console.error("[daily-picks] firestore_read_failed");
    res.status(502).json({
      ok: false,
      error: "firestore_read_failed",
      message: "Could not read scout_reports.",
    });
  }
}
