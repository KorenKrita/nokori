window.ARCH = window.ARCH || {};
window.ARCH.pr = {
  label: "commit e8118e82",
  title: "refactor: deepen architecture — delete shallow wrappers, decompose cold pipeline",
  add: 831, del: 1611, files: 16,
  nodes: {
    "cold": { files: 5, add: 774, del: 1380, paths: ["nokori/cold/_constants.py", "nokori/cold/_llm_call.py", "nokori/cold/integrate.py", "nokori/cold/pipeline.py", "nokori/cold/verify.py"] },
    "commands": { files: 1, add: 4, del: 4, paths: ["nokori/commands/test.py"] },
    "runtime": { files: 1, add: 0, del: 54, paths: ["nokori/runtime/selection.py"] },
    "search": { files: 2, add: 8, del: 125, paths: ["nokori/search/engine.py", "nokori/search/retrieve.py"] },
    "web-api": { files: 1, add: 5, del: 6, paths: ["nokori/web/api/retrieve.py"] }
  },
  contains: ["backend"],
  links: {
    add: [
      { s: "cold", t: "merge" },
      { s: "cold", t: "archive" }
    ],
    cut: [
      { s: "search", t: "runtime" }
    ]
  }
};
