// Optional IaC recipe for a NEW project. Connect the repository to the service
// named "hermes" separately, and define the three shared variables in Railway.
// No credential values or existing project IDs belong in this file.
import { defineRailway, project, service, volume } from "railway/iac";

export default defineRailway((ctx) => {
  const data = volume("hermes-data", { region: "us-west2", sizeMB: 1024 });
  const hermes = service("hermes", {
    // Omitting source preserves the user's own connected GitHub repository.
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile.railway" },
    start: "/opt/hermes/docker/entrypoint-dispatch.sh gateway run",
    healthcheck: "/api/status",
    healthcheckTimeout: 300,
    replicas: { "us-west2": 1 },
    deploy: {
      restartPolicyType: "ALWAYS",
      sleepApplication: false,
      requiredMountPath: "/opt/data",
    },
    volumeMounts: { "/opt/data": data },
    env: {
      PORT: "9119",
      HERMES_DASHBOARD_TOTP_AUTH_USERNAME: ctx.shared.HERMES_DASHBOARD_TOTP_AUTH_USERNAME,
      HERMES_DASHBOARD_TOTP_AUTH_PASSWORD: ctx.shared.HERMES_DASHBOARD_TOTP_AUTH_PASSWORD,
      OPENROUTER_API_KEY: ctx.shared.OPENROUTER_API_KEY,
    },
  });
  return project("hermes-agent", { resources: [hermes, data] });
});
