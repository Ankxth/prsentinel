// Plain-language descriptions of scanner rules, for people who are not security specialists.

export type Explained = { title: string; what: string; fix: string };

const CMD: Explained = {
  title: "Your code can be tricked into running commands",
  what: "Text that comes from outside (a user, a file or a web request) ends up inside a command that your program runs. Someone could add their own command and make the computer run it.",
  fix: "Pass the command as a list of separate parts instead of one string, avoid shell=True, and check any outside text strictly before using it.",
};

const RULES: Record<string, Explained> = {
  B602: CMD,
  B604: CMD,
  B605: CMD,
  B606: { ...CMD, title: "A program is started without checking where it comes from" },
  B607: { ...CMD, title: "A program is started using a partial path" },
  B608: {
    title: "A database question is built from plain text",
    what: "Text is glued straight into a database query. A person could type something that changes the query and read or damage data they should not reach.",
    fix: "Use parameterised queries (placeholders such as ?) so the database treats the text only as data.",
  },
  B307: {
    title: "Text is run as code",
    what: "The program uses eval to run text as code. If that text can come from outside, someone can make your program run anything.",
    fix: "Replace eval with a safe parser such as ast.literal_eval or with ordinary logic.",
  },
  B102: {
    title: "Text is run as code",
    what: "The program uses exec to run text as code. If that text can come from outside, someone can make your program run anything.",
    fix: "Avoid exec. Use ordinary functions, or a safe parser for the data you expect.",
  },
  B301: {
    title: "Data is loaded in an unsafe way",
    what: "Loading pickle data can run code hidden inside it. If the data comes from someone else, that is dangerous.",
    fix: "Use a plain format such as JSON for data you do not fully control.",
  },
  B403: {
    title: "The pickle module is used",
    what: "Pickle can run code hidden inside the data it loads, so it is only safe for data you created yourself.",
    fix: "Use JSON for data that comes from outside, or make sure the pickle data is always your own.",
  },
  B506: {
    title: "A file is loaded in an unsafe way",
    what: "yaml.load can build arbitrary objects, which can run code if the file is not trusted.",
    fix: "Use yaml.safe_load instead.",
  },
  B105: { title: "A password may be written in the code", what: "A fixed password or key is stored in the source. Anyone who can see the code can see it.", fix: "Move it to an environment variable or a secrets store, and change the password because it may already be exposed." },
  B106: { title: "A password may be written in the code", what: "A fixed password or key is passed in the source. Anyone who can see the code can see it.", fix: "Move it to an environment variable or a secrets store, and change the password because it may already be exposed." },
  B107: { title: "A password may be written in the code", what: "A default password is set in the source. Anyone who can see the code can see it.", fix: "Remove the default and read it from an environment variable or a secrets store." },
  B324: { title: "A weak fingerprint method is used", what: "MD5 or SHA1 are weak. They should not be used to protect passwords or to prove something was not changed.", fix: "Use SHA-256 or better, and a proper password hashing library such as bcrypt for passwords." },
  B501: { title: "Secure connection checks are turned off", what: "The program talks to a website without checking who it is really talking to, so someone in between could read or change the data.", fix: "Remove verify=False so certificates are checked." },
  B113: { title: "A web request can wait forever", what: "A web request has no time limit, so one slow website could freeze your program.", fix: "Add a timeout, for example timeout=10." },
  B311: { title: "A predictable random number is used", what: "Python's random module is not safe for passwords, tokens or anything an attacker must not guess.", fix: "Use the secrets module for anything security related." },
  B108: { title: "A shared temporary file location is used", what: "Files in a fixed folder such as /tmp can be read or replaced by other users of the machine.", fix: "Use the tempfile module, which creates private files." },
  B104: { title: "The program listens on every network address", what: "Binding to 0.0.0.0 can expose your service to the whole network.", fix: "Listen on 127.0.0.1 unless you really need outside access." },
  B201: { title: "Debug mode may be on", what: "Flask debug mode lets visitors run code on the server if it is left on in production.", fix: "Turn debug off outside your own computer." },
  B101: { title: "A check may disappear in production", what: "assert statements are removed when Python runs in optimised mode, so the check would silently stop working.", fix: "Use a normal if statement that raises an error." },
  B110: { title: "Errors are silently ignored", what: "A try/except block swallows every error without a trace, which can hide real problems.", fix: "Catch only the errors you expect and at least log them." },
};

const BY_TOOL: Record<string, Explained> = {
  "detect-secrets": {
    title: "A secret (such as a password or key) may be stored in your code",
    what: "Something that looks like a private key or password was found in the files. Anyone who can see the repository could use it.",
    fix: "Remove it from the code, store it in an environment variable, and replace it with a new one because the old one may be exposed.",
  },
  "pip-audit": {
    title: "A library you use has a known security problem",
    what: "One of the packages your project depends on has a publicly reported weakness.",
    fix: "Update the package to the fixed version named in the details.",
  },
};

export function explain(tool: string, ruleId: string, message: string): Explained {
  return (
    RULES[ruleId] ??
    BY_TOOL[tool] ?? {
      title: message || "A possible problem was found",
      what: message || "A scanner flagged this code as risky.",
      fix: "Open the technical details below for the scanner's own description.",
    }
  );
}

export const SEVERITY_PLAIN: Record<string, string> = {
  high: "Serious",
  medium: "Worth fixing",
  low: "Minor",
  unknown: "Not rated",
};
