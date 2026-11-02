"""Ponte fina para o setup oficial do REA, sem reproduzir seu instalador Hopper."""

# APIs exportadas verificadas em RBTecNet/rea@4838f44 (6.5.0).
# A instalação e a verificação de integridade permanecem integralmente no REA.
HOPPER_SETUP = r'''
import { pathToFileURL } from "node:url";
import { join } from "node:path";
const [root, mode] = process.argv.slice(1);
process.argv[1]=join(root,"scripts/rea.mjs");
const { runSetup } = await import(pathToFileURL(join(root,"dist/application/Setup.js")));
const { systemSetupHost } = await import(pathToFileURL(join(root,"dist/application/SetupHost.js")));
const { createSystemDoctorHost } = await import(pathToFileURL(join(root,"dist/doctorRuntime.js")));
const options = {approved:false,installHopper:true,structured:true,dryRun:true,
  clientIds:[],skillClientIds:[],installSkill:false,
  readinessScope:{clients:[],providers:["hopper"],skill:false}};
const host = () => new Proxy(systemSetupHost(createSystemDoctorHost(process.env),process.env), {
  get(target,key) {
    const allowed=new Set(["skillDestinations","macosVersion","linuxDistribution",
      "initialSetupState","installHopper","close","detectedClients","supportedClients",
      "clientNeedsConfigure","inspectClientConfiguration","skillNeedsInstall",
      "validateSkillDirectories","doctor"]);
    const value=Reflect.get(target,key);
    if (typeof value === "function" && !allowed.has(key))
      return () => {throw new Error("Capivara REA must not modify model clients or skills")};
    return typeof value === "function" ? value.bind(target) : value;
  }
});
const plan=await runSetup(options,host());
if (plan.plannedActions.some(action => action.kind !== "install_hopper"))
  throw new Error("Unexpected non-Hopper setup action; nothing was approved");
const result=mode === "apply" && plan.status === "planned"
  ? await runSetup({...options,approved:true,dryRun:false},host()) : plan;
console.log(JSON.stringify(result));
if (!["planned","ready"].includes(result.status)) process.exitCode=1;
'''
