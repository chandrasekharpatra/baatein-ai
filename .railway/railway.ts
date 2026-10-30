// Baatein on Railway: one service (web page, API and voice agent) in Singapore, with the SQLite
// database on a volume. Audio goes through LiveKit Cloud, not Railway. docs/DEPLOY.md
//
// Preview with `railway config plan`, then `railway config apply`. Set the secrets marked
// preserve() in the Railway dashboard first (sealed); they never go in this file.
import { defineRailway, github, preserve, project, service, volume } from "railway/iac";

const REGION = "asia-southeast1-eqsg3a"; // Singapore, nearest to India
const DOMAIN = "baatein.example.com"; // your Cloudflare subdomain
const PORT = "8000";

export default defineRailway(() => {
  const data = volume("baatein-data", { region: REGION, sizeMB: 1024 });

  const app = service("baatein", {
    // Built from the Dockerfile at the repo root.
    source: github("durga811/baatein-ai", { branch: "main" }),
    healthcheck: "/health",
    healthcheckTimeout: 120,
    // One copy only: a volume can't be shared, and calls need the one database.
    replicas: { [REGION]: 1 },
    volumeMounts: { "/app/data": data },
    domains: [{ domain: DOMAIN, port: Number(PORT) }],
    env: {
      PORT,
      // On a redeploy, live calls get a minute to finish before the old copy stops.
      RAILWAY_DEPLOYMENT_DRAINING_SECONDS: "60",
      BAATEIN_DATABASE__URL: "sqlite:////app/data/baatein.db",
      LIVEKIT_AGENT_NAME: "baatein",
      BAATEIN_LLM__MODEL: "claude-haiku-5-5",
      BAATEIN_FINALIZE__MODEL: "claude-haiku-5-5",
      BAATEIN_VOICE__DEFAULT_LANGUAGE: "hinglish",
      BAATEIN_EMBEDDING__PROVIDER: "pinecone",
      BAATEIN_EMBEDDING__MODEL: "llama-text-embed-v2",
      BAATEIN_EMBEDDING__DIMENSIONS: "2048",
      BAATEIN_AUTH__SIGNUP_ENABLED: "false",
      // Loaded on start when missing; never replaced by a redeploy.
      BAATEIN_SEED_FILES: [
        "seeds/demo/asha_new.json",
        "seeds/demo/meera_interview.json",
        "seeds/demo/vikram_new_dad.json",
        "seeds/demo/kamala_elder.json",
      ].join(" "),
      // Secrets, set (sealed) in the dashboard.
      ANTHROPIC_API_KEY: preserve(),
      SARVAM_API_KEY: preserve(),
      PINECONE_API_KEY: preserve(),
      LIVEKIT_URL: preserve(),
      LIVEKIT_API_KEY: preserve(),
      LIVEKIT_API_SECRET: preserve(),
      BAATEIN_AUTH__JWT_SECRET: preserve(),
      BAATEIN_AUTH__SEED_PASSWORD: preserve(),
      BAATEIN_AUTH__ADMIN_EMAILS: preserve(),
    },
  });

  return project("baatein", { resources: [app, data] });
});
