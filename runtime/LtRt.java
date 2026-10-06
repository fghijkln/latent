import java.util.*;
import java.io.*;
import java.lang.reflect.Array;

/**
 * Latent Java-backend runtime.
 *
 * Value model: every Latent value is an Object
 *   num    -> Double      (the language has a single float64 number type)
 *   str    -> String
 *   bool   -> Boolean
 *   nil    -> null
 *   list   -> ArrayList<Object>
 *   map    -> LinkedHashMap<String, Object>
 *   py handle -> PyHandle (lazy: python3 starts on first actual use)
 */
public class LtRt {

    // ---------------- truthiness ----------------
    public static boolean truthy(Object v) {
        if (v == null) return false;
        if (v instanceof Boolean) return (Boolean) v;
        if (v instanceof Double) {
            double d = (Double) v;
            return d != 0.0 && !Double.isNaN(d);
        }
        if (v instanceof String) return !((String) v).isEmpty();
        if (v instanceof List) return !((List<?>) v).isEmpty();
        if (v instanceof Map) return !((Map<?, ?>) v).isEmpty();
        if (v instanceof PyHandle) return Daemon.inst().truthy((PyHandle) v);
        return true;
    }

    static double num(Object v, String op) {
        if (v instanceof Double) return (Double) v;
        throw new RuntimeException("bad " + op + " operand: " + typeName(v));
    }

    static String typeName(Object v) {
        if (v == null) return "nil";
        if (v instanceof Double) return "num";
        if (v instanceof String) return "str";
        if (v instanceof Boolean) return "bool";
        if (v instanceof List) return "list";
        if (v instanceof Map) return "map";
        if (v instanceof LtFunction) return "function";
        if (v instanceof PyHandle) return "py";
        if (v instanceof JReflect.JClass) return "java class";
        if (v instanceof JReflect.JObj) return "java obj";
        return v.getClass().getSimpleName();
    }

    // ---------------- arithmetic ----------------
    public static Object add(Object a, Object b) {
        if (a instanceof PyHandle || b instanceof PyHandle)
            return Daemon.inst().binop("__add__", a, b);
        if (a instanceof Double && b instanceof Double) return (Double) a + (Double) b;
        if (a instanceof String && b instanceof String) return (String) a + (String) b;
        if (a instanceof List && b instanceof List) {
            List<Object> r = new ArrayList<>((List<?>) a);
            r.addAll((List<?>) b);
            return r;
        }
        throw new RuntimeException("bad + operands: " + typeName(a) + ", " + typeName(b));
    }

    public static Object sub(Object a, Object b) {
        if (a instanceof PyHandle || b instanceof PyHandle)
            return Daemon.inst().binop("__sub__", a, b);
        return num(a, "-") - num(b, "-");
    }

    public static Object mul(Object a, Object b) {
        if (a instanceof PyHandle || b instanceof PyHandle)
            return Daemon.inst().binop("__mul__", a, b);
        return num(a, "*") * num(b, "*");
    }

    public static Object div(Object a, Object b) {
        if (a instanceof PyHandle || b instanceof PyHandle)
            return Daemon.inst().binop("__truediv__", a, b);
        double x = num(a, "/"), y = num(b, "/");
        if (y == 0) throw new RuntimeException("division by zero");
        return x / y;
    }

    public static Object mod(Object a, Object b) {
        if (a instanceof PyHandle || b instanceof PyHandle)
            return Daemon.inst().binop("__mod__", a, b);
        double x = num(a, "%"), y = num(b, "%");
        if (y == 0) throw new RuntimeException("division by zero");
        return x - y * Math.floor(x / y); // floored, matches Python backend
    }

    public static Object pow(Object a, Object b) {
        if (a instanceof PyHandle || b instanceof PyHandle)
            return Daemon.inst().binop("__pow__", a, b);
        return Math.pow(num(a, "**"), num(b, "**"));
    }

    public static Object neg(Object a) {
        if (a instanceof PyHandle) return Daemon.inst().binop("__neg__", a);
        return -num(a, "unary -");
    }

    // ---------------- comparison ----------------
    public static boolean eq(Object a, Object b) {
        if (a == null || b == null) return a == b;
        // remote / opaque handles: identity, matches Python backend
        if (a instanceof PyHandle || b instanceof PyHandle) return a == b;
        if (a instanceof LtObj || b instanceof LtObj) return a == b;
        if (a instanceof LtClass || b instanceof LtClass) return a == b;
        if (a instanceof LtFunction || b instanceof LtFunction) return a == b;
        if (a instanceof JReflect.JClass || b instanceof JReflect.JClass) return a == b;
        if (a instanceof JReflect.JObj || b instanceof JReflect.JObj) return a == b;
        if (a instanceof Double && b instanceof Double) {
            double x = (Double) a, y = (Double) b;
            if (Double.isNaN(x) || Double.isNaN(y)) return false;
            return x == y;
        }
        if (a instanceof String && b instanceof String) return a.equals(b);
        if (a instanceof Boolean && b instanceof Boolean) return a.equals(b);
        if (a instanceof List && b instanceof List) {
            List<?> la = (List<?>) a, lb = (List<?>) b;
            if (la.size() != lb.size()) return false;
            for (int i = 0; i < la.size(); i++)
                if (!eq(la.get(i), lb.get(i))) return false;
            return true;
        }
        if (a instanceof Map && b instanceof Map) {
            Map<?, ?> ma = (Map<?, ?>) a, mb = (Map<?, ?>) b;
            if (!ma.keySet().equals(mb.keySet())) return false;
            for (Object k : ma.keySet())
                if (!eq(ma.get(k), mb.get(k))) return false;
            return true;
        }
        return false;
    }

    static int cmp(Object a, Object b, String op) {
        if (a instanceof Double && b instanceof Double)
            return Double.compare((Double) a, (Double) b);
        if (a instanceof String && b instanceof String)
            return ((String) a).compareTo((String) b);
        throw new RuntimeException("bad " + op + " operands: " + typeName(a) + ", " + typeName(b));
    }

    public static boolean lt(Object a, Object b) { return cmp(a, b, "<") < 0; }
    public static boolean lte(Object a, Object b) { return cmp(a, b, "<=") <= 0; }
    public static boolean gt(Object a, Object b) { return cmp(a, b, ">") > 0; }
    public static boolean gte(Object a, Object b) { return cmp(a, b, ">=") >= 0; }

    // ---------------- repr / say ----------------
    /** Shortest-roundtrip float formatting, Python-repr style. */
    static String numStr(double d) {
        if (Double.isNaN(d)) return "nan";
        if (Double.isInfinite(d)) return d > 0 ? "inf" : "-inf";
        if (d == Math.rint(d) && Math.abs(d) < 1e16) return Long.toString((long) d);
        String s = Double.toString(d); // 3.14 | 1.0E16 | 1.234E-7
        int e = s.indexOf('E');
        if (e < 0) return s;
        String m = s.substring(0, e);
        if (m.endsWith(".0")) m = m.substring(0, m.length() - 2);
        String exp = s.substring(e + 1);
        boolean neg = exp.startsWith("-");
        String digits = neg ? exp.substring(1) : exp;
        while (digits.length() < 2) digits = "0" + digits;
        return m + "e" + (neg ? "-" : "+") + digits;
    }

    static String escape(String s) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\t': sb.append("\\t"); break;
                case '\r': sb.append("\\r"); break;
                default:
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        return sb.toString();
    }

    public static String repr(Object v) {
        if (v == null) return "nil";
        if (v instanceof Boolean) return (Boolean) v ? "true" : "false";
        if (v instanceof Double) return numStr((Double) v);
        if (v instanceof String) return (String) v;
        if (v instanceof PyHandle) return Daemon.inst().repr((PyHandle) v);
        if (v instanceof LtClass) return "<class " + ((LtClass) v).name + ">";
        if (v instanceof LtFunction)
            return ((LtFunction) v).display;
        if (v instanceof LtObj) return "<" + ((LtObj) v).cls.name + " object>";
        if (v instanceof JReflect.JClass || v instanceof JReflect.JObj)
            return JReflect.repr(v);
        if (v instanceof List) {
            StringBuilder sb = new StringBuilder("[");
            boolean first = true;
            for (Object x : (List<?>) v) {
                if (!first) sb.append(", ");
                sb.append(reprQ(x));
                first = false;
            }
            return sb.append("]").toString();
        }
        if (v instanceof Map) {
            StringBuilder sb = new StringBuilder("{");
            boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                if (!first) sb.append(", ");
                sb.append(reprQ(e.getKey())).append(": ").append(reprQ(e.getValue()));
                first = false;
            }
            return sb.append("}").toString();
        }
        return String.valueOf(v);
    }

    static String reprQ(Object v) {
        if (v instanceof String) return "\"" + escape((String) v) + "\"";
        return repr(v);
    }

    public static Object say(Object v) {
        System.out.println(repr(v));
        return null;
    }

    // ---------------- builtins ----------------
    public static Object len(Object x) {
        if (x instanceof List) return (double) ((List<?>) x).size();
        if (x instanceof Map) return (double) ((Map<?, ?>) x).size();
        if (x instanceof String) return (double) ((String) x).length();
        throw new RuntimeException("len() of " + typeName(x));
    }

    public static Object range(Object n) { return range(0.0, n); }

    public static Object range(Object a, Object b) {
        double lo = num(a, "range"), hi = num(b, "range");
        List<Object> r = new ArrayList<>();
        for (long i = (long) lo; i < (long) hi; i++) r.add((double) i);
        return r;
    }

    public static Object strOf(Object x) { return repr(x); }

    public static Object toInt(Object x) {
        if (x instanceof Boolean) return (Boolean) x ? 1.0 : 0.0;
        if (x instanceof Double) {
            double d = (Double) x;
            return d >= 0 ? Math.floor(d) : Math.ceil(d);
        }
        if (x instanceof String) {
            try { return (double) Long.parseLong(((String) x).trim()); }
            catch (NumberFormatException e) {
                throw new RuntimeException("int() of " + repr(x));
            }
        }
        throw new RuntimeException("int() of " + typeName(x));
    }

    @SuppressWarnings("unchecked")
    public static Object push(Object xs, Object x) {
        if (!(xs instanceof List)) throw new RuntimeException("push() target must be a list");
        ((List<Object>) xs).add(x);
        return null;
    }

    public static Object keys(Object m) {
        if (!(m instanceof Map)) throw new RuntimeException("keys() of non-map");
        return new ArrayList<>(((Map<?, ?>) m).keySet());
    }

    public static Object listOf(Object... xs) {
        return new ArrayList<>(Arrays.asList(xs));
    }

    public static Object mapOf(Object... kvs) {
        LinkedHashMap<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kvs.length; i += 2) m.put((String) kvs[i], kvs[i + 1]);
        return m;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> iter(Object x) {
        if (x instanceof List) return (List<Object>) x;
        if (x instanceof String) {
            String s = (String) x;
            List<Object> r = new ArrayList<>();
            for (int i = 0; i < s.length(); i++) r.add(String.valueOf(s.charAt(i)));
            return r;
        }
        if (x instanceof Map) return new ArrayList<>(((Map<?, ?>) x).keySet());
        if (x instanceof JReflect.JObj) { // java List / array
            Object o = ((JReflect.JObj) x).o;
            if (o instanceof List || o.getClass().isArray()) {
                Object w = JReflect.deepWrap(o);
                @SuppressWarnings("unchecked")
                List<Object> r = (List<Object>) w;
                return r;
            }
        }
        throw new RuntimeException("cannot iterate " + typeName(x));
    }

    // ---------------- py interop ----------------
    public static class PyHandle {
        final String module; // non-null for `py "mod"` handles
        final long id;       // remote object id, -1 for module handles
        PyHandle(String module) { this.module = module; this.id = -1; }
        PyHandle(long id) { this.module = null; this.id = id; }
        boolean isModule() { return module != null; }
    }

    // ---------------- Latent classes (v0.3) ----------------
    /** A Latent method: uniform (self, args) shape so LtClass can hold
    a name -> implementation table without reflection. */
    public interface LtMethod {
        Object call(Object self, Object[] args);
    }

    /** A first-class Latent function value with an immutable lexical parent. */
    public interface LtBody {
        Object call(Object[] args);
    }
    /** Marker for a named actual argument; the value is already evaluated. */
    public static class NamedArg {
        final String name;
        final Object value;
        NamedArg(String name, Object value) {
            this.name = name;
            this.value = value;
        }
    }
    public static class ArgumentError extends RuntimeException {
        ArgumentError(String message) { super(message); }
    }
    public static Object named(String name, Object value) {
        return new NamedArg(name, value);
    }
    private static boolean hasNamedArgs(Object[] args) {
        for (Object arg : args) if (arg instanceof NamedArg) return true;
        return false;
    }
    private static void rejectNamedInteropArgs(Object[] args) {
        if (hasNamedArgs(args))
            throw new ArgumentError(
                "named arguments are not supported for Python/Java interop calls");
    }
    private static Object[] bindArgs(String[] params, Object[] args, String name) {
        if (!hasNamedArgs(args)) {
            if (args.length != params.length)
                throw new ArgumentError(name + "() takes " + params.length +
                    " args, got " + args.length);
            return args;
        }
        Object[] values = new Object[params.length];
        boolean[] supplied = new boolean[params.length];
        Set<String> seenNames = new HashSet<>();
        int pos = 0;
        for (Object arg : args) {
            if (arg instanceof NamedArg) {
                NamedArg named = (NamedArg) arg;
                if (!seenNames.add(named.name))
                    throw new ArgumentError(name +
                        "() got duplicate named argument '" + named.name + "'");
                int index = Arrays.asList(params).indexOf(named.name);
                if (index < 0)
                    throw new ArgumentError(name +
                        "() got unexpected named argument '" + named.name + "'");
                if (supplied[index])
                    throw new ArgumentError(name +
                        "() got multiple values for argument '" + named.name + "'");
                values[index] = named.value;
                supplied[index] = true;
            } else {
                if (pos >= params.length)
                    throw new ArgumentError(name + "() takes " + params.length +
                        " args, got " + (pos + 1));
                values[pos] = arg;
                supplied[pos] = true;
                pos++;
            }
        }
        for (int i = 0; i < params.length; i++) {
            if (!supplied[i])
                throw new ArgumentError(name +
                    "() missing required argument '" + params[i] + "'");
        }
        return values;
    }
    public static class LtFunction {
        final int arity;
        final String name;
        final String display;
        final String[] params;
        final LtBody body;
        LtFunction(String[] params, String name, LtBody body) {
            this(params, name, "<function " + name + ">", body);
        }
        LtFunction(String[] params, String name, String display, LtBody body) {
            this.params = params.clone();
            this.arity = params.length;
            this.name = name;
            this.display = display;
            this.body = body;
        }
        Object invoke(Object[] args) {
            return body.call(bindArgs(params, args, name));
        }
    }
    /** Per-invocation binding cells; nested functions retain this frame. */
    public static class Env {
        final Env parent;
        final Map<String, Object> values = new LinkedHashMap<>();
        public Env(Env parent, String[] names) {
            this.parent = parent;
            for (String name : names) values.put(name, null);
        }
        public void setLocal(String name, Object value) {
            if (!values.containsKey(name))
                throw new RuntimeException("unknown local binding '" + name + "'");
            values.put(name, value);
        }
        public void setEnclosing(String name, Object value) {
            for (Env frame = parent; frame != null; frame = frame.parent) {
                if (frame.values.containsKey(name)) {
                    frame.values.put(name, value);
                    return;
                }
            }
            throw new RuntimeException("unknown enclosing binding '" + name + "'");
        }
        public Object get(String name) {
            for (Env frame = this; frame != null; frame = frame.parent)
                if (frame.values.containsKey(name)) return frame.values.get(name);
            throw new RuntimeException("unknown lexical binding '" + name + "'");
        }
    }
    public static LtFunction function(String[] params, String name, LtBody body) {
        return new LtFunction(params, name, body);
    }
    public static Object callValue(Object value, Object... args) {
        if (!(value instanceof LtFunction))
            throw new RuntimeException("call on non-function value");
        return ((LtFunction) value).invoke(args);
    }

    public static class LtClass {
        public final String name;
        public final LtClass parent;
        public final Map<String, LtMethod> methods;
        public final Map<String, Integer> methodArities;
        public final Map<String, String[]> methodParameters;
        LtClass(String name, LtClass parent, Map<String, LtMethod> methods,
                Map<String, Integer> methodArities,
                Map<String, String[]> methodParameters) {
            this.name = name;
            this.parent = parent;
            this.methods = methods;
            this.methodArities = methodArities;
            this.methodParameters = methodParameters;
        }
    }

    public static class LtObj {
        public final LtClass cls;
        public final Map<String, Object> fields = new LinkedHashMap<>();
        LtObj(LtClass cls) { this.cls = cls; }
    }

    public static LtClass makeClass(String name, LtClass parent,
                                    String[] names, LtMethod[] methods,
                                    int[] arities, String[][] parameters) {
        Map<String, LtMethod> m = new LinkedHashMap<>();
        Map<String, Integer> a = new LinkedHashMap<>();
        Map<String, String[]> p = new LinkedHashMap<>();
        for (int i = 0; i < names.length; i++) {
            m.put(names[i], methods[i]);
            a.put(names[i], arities[i]);
            p.put(names[i], parameters[i].clone());
        }
        return new LtClass(name, parent, m, a, p);
    }

    static LtMethod findMethod(LtClass cls, String name) {
        for (LtClass c = cls; c != null; c = c.parent) {
            LtMethod method = c.methods.get(name);
            if (method != null) return method;
        }
        return null;
    }

    static int findMethodArity(LtClass cls, String name) {
        for (LtClass c = cls; c != null; c = c.parent) {
            Integer arity = c.methodArities.get(name);
            if (arity != null) return arity;
        }
        return -1;
    }

    static String[] findMethodParameters(LtClass cls, String name) {
        for (LtClass c = cls; c != null; c = c.parent) {
            String[] params = c.methodParameters.get(name);
            if (params != null) return params;
        }
        return new String[0];
    }

    /** Dispatch a Latent method beginning at the current class's parent. */
    public static Object superCall(Object receiver, LtClass owner,
                                   String method, Object... args) {
        if (!(receiver instanceof LtObj) || owner == null)
            throw new RuntimeException("super call requires a Latent instance and class");
        LtClass actual = ((LtObj) receiver).cls;
        while (actual != null && actual != owner) actual = actual.parent;
        if (actual == null || owner.parent == null)
            throw new RuntimeException("super call owner is not in the instance inheritance chain");
        LtMethod target = findMethod(owner.parent, method);
        if (target == null)
            throw new RuntimeException("no parent method '" + method + "' on class " + owner.name);
        return target.call(receiver,
            bindArgs(findMethodParameters(owner.parent, method), args, method));
    }

    public static Object pymod(Object name) {
        if (!(name instanceof String))
            throw new RuntimeException("py module name must be a string");
        return new PyHandle((String) name); // lazy: nothing starts yet
    }

    static PyHandle asHandle(Object h) {
        if (h instanceof PyHandle) return (PyHandle) h;
        throw new RuntimeException("attribute access on non-py value");
    }

    public static Object pyget(Object h, String attr) {
        return Daemon.inst().get(asHandle(h), attr);
    }

    public static Object pycall(Object h, String attr, Object... args) {
        return Daemon.inst().call(asHandle(h), attr, args);
    }

    // ---------------- java interop ----------------
    public static Object jclass(Object name) {
        if (!(name instanceof String))
            throw new RuntimeException("java class name must be a string");
        return new JReflect.JClass((String) name); // lazy: Class.forName on first use
    }

    /** Unified attribute access: py handles and java handles. */
    public static Object wgetattr(Object h, String attr) {
        if (h instanceof PyHandle) return pyget(h, attr);
        if (h instanceof LtObj) {
            LtObj o = (LtObj) h;
            Map<String, Object> f = o.fields;
            if (f.containsKey(attr)) return f.get(attr);
            LtMethod method = findMethod(o.cls, attr);
            if (method != null) {
                String[] params = findMethodParameters(o.cls, attr);
                return new LtFunction(params, attr,
                    "<bound method " + o.cls.name + "." + attr + ">",
                    args -> method.call(o, args));
            }
            throw new RuntimeException("no field '" + attr + "'");
        }
        if (h instanceof LtClass)
            throw new RuntimeException("cannot read fields on a class");
        if (h instanceof JReflect.JClass)
            return JReflect.getField((JReflect.JClass) h, attr);
        if (h instanceof JReflect.JObj)
            return JReflect.getField((JReflect.JObj) h, attr);
        throw new RuntimeException("attribute access on non-handle value: " + typeName(h));
    }

    /** Unified call: py handles, java static/instance methods, C.new() constructors. */
    public static Object wcall(Object h, String attr, Object... args) {
        if (h instanceof PyHandle) {
            rejectNamedInteropArgs(args);
            return pycall(h, attr, args);
        }
        if (h instanceof LtClass) {
            LtClass c = (LtClass) h;
            if (attr.equals("new")) {
                LtObj o = new LtObj(c);
                LtMethod init = findMethod(c, "init");
                String[] params = findMethodParameters(c, "init");
                if (init != null) init.call(o, bindArgs(params, args, c.name + ".new"));
                else if (args.length > 0) bindArgs(new String[0], args, c.name + ".new");
                return o;
            }
            throw new RuntimeException("no class-level method '" + attr +
                "' on class " + c.name);
        }
        if (h instanceof LtObj) {
            LtObj o = (LtObj) h;
            LtMethod m = findMethod(o.cls, attr);
            if (m == null)
                throw new RuntimeException("no method '" + attr +
                    "' on " + o.cls.name);
            return m.call(o, bindArgs(findMethodParameters(o.cls, attr), args, attr));
        }
        if (h instanceof JReflect.JClass) {
            rejectNamedInteropArgs(args);
            if (attr.equals("new"))
                return JReflect.construct((JReflect.JClass) h, args);
            return JReflect.callStatic((JReflect.JClass) h, attr, args);
        }
        if (h instanceof JReflect.JObj) {
            rejectNamedInteropArgs(args);
            return JReflect.call((JReflect.JObj) h, attr, args);
        }
        throw new RuntimeException("call on non-handle value: " + typeName(h));
    }

    public static Object wsetattr(Object h, String attr, Object v) {
        if (h instanceof LtObj) {
            ((LtObj) h).fields.put(attr, v);
            return null;
        }
        if (h instanceof PyHandle)
            return Daemon.inst().setattr((PyHandle) h, attr, v);
        if (h instanceof JReflect.JObj)
            return JReflect.setField((JReflect.JObj) h, attr, v);
        if (h instanceof JReflect.JClass)
            return JReflect.setField((JReflect.JClass) h, attr, v);
        throw new RuntimeException("cannot set attribute on " + typeName(h));
    }

    @SuppressWarnings("unchecked")
    public static Object wsetindex(Object o, Object k, Object v) {
        if (o instanceof PyHandle)
            return Daemon.inst().setitem((PyHandle) o, k, v);
        if (o instanceof JReflect.JObj) {
            Object u = ((JReflect.JObj) o).o;
            if (u instanceof List) {
                List<Object> l = (List<Object>) u;
                l.set(toIndex(k, l.size()), v);
                return null;
            }
            if (u instanceof Map) {
                ((Map<Object, Object>) u).put(k, v);
                return null;
            }
            throw new RuntimeException("cannot index-assign java value of type " +
                u.getClass().getName());
        }
        if (o instanceof List) {
            List<Object> l = (List<Object>) o;
            l.set(toIndex(k, l.size()), v);
            return null;
        }
        if (o instanceof Map) {
            ((Map<Object, Object>) o).put(k, v);
            return null;
        }
        throw new RuntimeException("cannot index-assign " + typeName(o));
    }

    /** Indexing: xs[i] / m[k] / s[i]. Negative indices count from the end. */
    public static Object index(Object o, Object k) {
        if (o instanceof PyHandle)
            return Daemon.inst().getitem((PyHandle) o, k);
        if (o instanceof JReflect.JObj) {
            Object u = ((JReflect.JObj) o).o;
            if (u instanceof List) {
                List<?> l = (List<?>) u;
                return JReflect.wrap(l.get(toIndex(k, l.size())));
            }
            if (u instanceof Map) {
                Map<?, ?> mp = (Map<?, ?>) u;
                if (!mp.containsKey(k))
                    throw new RuntimeException("key not found: " + repr(k));
                return JReflect.wrap(mp.get(k));
            }
            throw new RuntimeException("cannot index java value of type " +
                u.getClass().getName());
        }
        if (o instanceof List) {
            List<?> l = (List<?>) o;
            return l.get(toIndex(k, l.size()));
        }
        if (o instanceof Map) {
            Map<?, ?> mp = (Map<?, ?>) o;
            if (!mp.containsKey(k))
                throw new RuntimeException("key not found: " + repr(k));
            return mp.get(k);
        }
        if (o instanceof String) {
            String s = (String) o;
            int i = toIndex(k, s.length());
            return s.substring(i, i + 1);
        }
        throw new RuntimeException("cannot index " + typeName(o));
    }

    static int toIndex(Object k, int n) {
        if (k instanceof Boolean || !(k instanceof Number))
            throw new RuntimeException("index must be an integer");
        double d = ((Number) k).doubleValue();
        if (d != Math.rint(d))
            throw new RuntimeException("index must be an integer");
        int i = (int) d;
        if (i < 0) i += n;
        if (i < 0 || i >= n)
            throw new RuntimeException("index out of range: " + (int) d);
        return i;
    }

    public static void shutdown() {
        Daemon.shutdown();
    }

    // ---------------- lazy python daemon ----------------
    static class Daemon {
        private static Daemon instance;
        private Process proc;
        private BufferedWriter out;
        private BufferedReader in;

        static synchronized Daemon inst() {
            if (instance == null) {
                instance = new Daemon();
                instance.start();
            }
            return instance;
        }

        static synchronized void shutdown() {
            if (instance != null) {
                instance.close();
                instance = null;
            }
        }

        private void start() {
            String script = findScript();
            try {
                ProcessBuilder pb = new ProcessBuilder("python3", "-u", script);
                pb.redirectError(ProcessBuilder.Redirect.INHERIT);
                proc = pb.start();
                out = new BufferedWriter(new OutputStreamWriter(proc.getOutputStream(), "UTF-8"));
                in = new BufferedReader(new InputStreamReader(proc.getInputStream(), "UTF-8"));
            } catch (IOException e) {
                throw new RuntimeException(
                    "cannot start python3 (needed for py ...): " + e.getMessage());
            }
            Runtime.getRuntime().addShutdownHook(new Thread(() -> {
                try { Daemon.shutdown(); } catch (Exception ignored) {}
            }));
        }

        private static String findScript() {
            String p = System.getProperty("latent.pydaemon");
            if (p != null) return p;
            p = System.getenv("LATENT_PY");
            if (p != null && !p.isEmpty()) return p;
            try {
                String loc = LtRt.class.getProtectionDomain().getCodeSource()
                        .getLocation().toURI().getPath();
                File dir = new File(loc);
                if (dir.isFile()) dir = dir.getParentFile();
                File f = new File(dir, "ltpy.py");
                if (f.isFile()) return f.getAbsolutePath();
            } catch (Exception ignored) {}
            File cwd = new File("ltpy.py");
            if (cwd.isFile()) return cwd.getAbsolutePath();
            throw new RuntimeException(
                "ltpy.py not found: set LATENT_PY env or -Dlatent.pydaemon=<path>");
        }

        private void close() {
            try { sendRaw("{\"op\":\"quit\"}"); } catch (Exception ignored) {}
            try { if (out != null) out.close(); } catch (Exception ignored) {}
            try { if (in != null) in.close(); } catch (Exception ignored) {}
            if (proc != null) proc.destroy();
            proc = null;
        }

        private void sendRaw(String s) throws IOException {
            out.write(s);
            out.write("\n");
            out.flush();
        }

        /** One request -> decoded "value", or throws on daemon error. */
        private synchronized Object exchange(String req) {
            try {
                sendRaw(req);
                String line = in.readLine();
                if (line == null) throw new RuntimeException("python daemon died");
                Object resp = Json.parse(line);
                @SuppressWarnings("unchecked")
                Map<String, Object> m = (Map<String, Object>) resp;
                if (m.containsKey("error"))
                    throw new RuntimeException("python error: " + m.get("error"));
                return decodeValue(m.get("value"));
            } catch (IOException e) {
                throw new RuntimeException("python daemon io error: " + e.getMessage());
            }
        }

        private static String targetJson(PyHandle h) {
            if (h.isModule()) return "{\"mod\":" + Json.str(h.module) + "}";
            return "{\"id\":" + h.id + "}";
        }

        Object get(PyHandle h, String attr) {
            return exchange("{\"op\":\"get\",\"target\":" + targetJson(h) +
                            ",\"attr\":" + Json.str(attr) + "}");
        }

        Object call(PyHandle h, String attr, Object[] args) {
            StringBuilder sb = new StringBuilder(
                "{\"op\":\"call\",\"target\":" + targetJson(h) +
                ",\"attr\":" + Json.str(attr) + ",\"args\":[");
            for (int i = 0; i < args.length; i++) {
                if (i > 0) sb.append(",");
                sb.append(encodeArg(args[i]));
            }
            return exchange(sb.append("]}").toString());
        }

        String repr(PyHandle h) {
            Object r = exchange("{\"op\":\"repr\",\"target\":" + targetJson(h) + "}");
            return r == null ? "nil" : String.valueOf(r);
        }

        Object getitem(PyHandle h, Object key) {
            return exchange("{\"op\":\"getitem\",\"target\":" + targetJson(h) +
                ",\"key\":" + encodeArg(key) + "}");
        }

        Object setattr(PyHandle h, String attr, Object v) {
            return exchange("{\"op\":\"setattr\",\"target\":" + targetJson(h) +
                ",\"attr\":" + Json.str(attr) + ",\"value\":" + encodeArg(v) + "}");
        }

        Object setitem(PyHandle h, Object key, Object v) {
            return exchange("{\"op\":\"setitem\",\"target\":" + targetJson(h) +
                ",\"key\":" + encodeArg(key) + ",\"value\":" + encodeArg(v) + "}");
        }

        Object binop(String dunder, Object... args) {
            StringBuilder sb = new StringBuilder(
                "{\"op\":\"binop\",\"name\":" + Json.str(dunder) + ",\"args\":[");
            for (int i = 0; i < args.length; i++) {
                if (i > 0) sb.append(",");
                sb.append(encodeArg(args[i]));
            }
            return exchange(sb.append("]}").toString());
        }

        boolean truthy(PyHandle h) {
            Object r = exchange("{\"op\":\"truthy\",\"target\":" + targetJson(h) + "}");
            return r instanceof Boolean && (Boolean) r;
        }

        private static String encodeArg(Object v) {
            if (v instanceof PyHandle) {
                PyHandle h = (PyHandle) v;
                return h.isModule() ? "{\"__mod\":" + Json.str(h.module) + "}"
                                    : "{\"__ref\":" + h.id + "}";
            }
            return Json.encode(v);
        }

        @SuppressWarnings("unchecked")
        private static Object decodeValue(Object v) {
            if (v instanceof Map) {
                Map<String, Object> m = (Map<String, Object>) v;
                if (m.containsKey("__ref"))
                    return new PyHandle(((Double) m.get("__ref")).longValue());
                if (m.containsKey("__num")) {
                    String k = (String) m.get("__num");
                    if (k.equals("nan")) return Double.NaN;
                    if (k.equals("inf")) return Double.POSITIVE_INFINITY;
                    return Double.NEGATIVE_INFINITY;
                }
                LinkedHashMap<String, Object> r = new LinkedHashMap<>();
                for (Map.Entry<String, Object> e : m.entrySet())
                    r.put(e.getKey(), decodeValue(e.getValue()));
                return r;
            }
            if (v instanceof List) {
                List<Object> r = new ArrayList<>();
                for (Object x : (List<?>) v) r.add(decodeValue(x));
                return r;
            }
            return v; // Double, String, Boolean, null
        }
    }

    // ---------------- minimal JSON ----------------
    static class Json {
        static String str(String s) {
            StringBuilder sb = new StringBuilder("\"");
            for (int i = 0; i < s.length(); i++) {
                char c = s.charAt(i);
                switch (c) {
                    case '"': sb.append("\\\""); break;
                    case '\\': sb.append("\\\\"); break;
                    case '\n': sb.append("\\n"); break;
                    case '\t': sb.append("\\t"); break;
                    case '\r': sb.append("\\r"); break;
                    default:
                        if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                        else sb.append(c);
                }
            }
            return sb.append("\"").toString();
        }

        static String encode(Object v) {
            if (v == null) return "null";
            if (v instanceof Boolean) return v.toString();
            if (v instanceof Double) {
                double d = (Double) v;
                if (Double.isNaN(d)) return "{\"__num\":\"nan\"}";
                if (Double.isInfinite(d))
                    return "{\"__num\":\"" + (d > 0 ? "inf" : "-inf") + "\"}";
                return numStr(d);
            }
            if (v instanceof String) return str((String) v);
            if (v instanceof List) {
                StringBuilder sb = new StringBuilder("[");
                boolean first = true;
                for (Object x : (List<?>) v) {
                    if (!first) sb.append(",");
                    sb.append(encode(x));
                    first = false;
                }
                return sb.append("]").toString();
            }
            if (v instanceof Map) {
                StringBuilder sb = new StringBuilder("{");
                boolean first = true;
                for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                    if (!first) sb.append(",");
                    sb.append(str(String.valueOf(e.getKey()))).append(":")
                      .append(encode(e.getValue()));
                    first = false;
                }
                return sb.append("}").toString();
            }
            throw new RuntimeException("cannot send to python: " + typeName(v));
        }

        static Object parse(String s) { return new P(s).value(); }

        static class P {
            final String s;
            int i;
            P(String s) { this.s = s; }
            void ws() { while (i < s.length() && Character.isWhitespace(s.charAt(i))) i++; }
            Object value() {
                ws();
                if (i >= s.length()) throw new RuntimeException("bad json");
                char c = s.charAt(i);
                if (c == '{') return obj();
                if (c == '[') return arr();
                if (c == '"') return string();
                if (c == 't') { i += 4; return Boolean.TRUE; }
                if (c == 'f') { i += 5; return Boolean.FALSE; }
                if (c == 'n') { i += 4; return null; }
                return number();
            }
            Map<String, Object> obj() {
                LinkedHashMap<String, Object> m = new LinkedHashMap<>();
                i++; // {
                ws();
                if (s.charAt(i) == '}') { i++; return m; }
                while (true) {
                    ws();
                    String k = string();
                    ws();
                    i++; // :
                    m.put(k, value());
                    ws();
                    char c = s.charAt(i++);
                    if (c == '}') return m;
                }
            }
            List<Object> arr() {
                List<Object> l = new ArrayList<>();
                i++; // [
                ws();
                if (s.charAt(i) == ']') { i++; return l; }
                while (true) {
                    l.add(value());
                    ws();
                    char c = s.charAt(i++);
                    if (c == ']') return l;
                }
            }
            String string() {
                StringBuilder sb = new StringBuilder();
                i++; // "
                while (true) {
                    char c = s.charAt(i++);
                    if (c == '"') return sb.toString();
                    if (c == '\\') {
                        char e = s.charAt(i++);
                        switch (e) {
                            case '"': sb.append('"'); break;
                            case '\\': sb.append('\\'); break;
                            case '/': sb.append('/'); break;
                            case 'n': sb.append('\n'); break;
                            case 't': sb.append('\t'); break;
                            case 'r': sb.append('\r'); break;
                            case 'u':
                                sb.append((char) Integer.parseInt(s.substring(i, i + 4), 16));
                                i += 4;
                                break;
                            default: sb.append(e);
                        }
                    } else sb.append(c);
                }
            }
            Double number() {
                int j = i;
                while (j < s.length() && "-+0123456789.eE".indexOf(s.charAt(j)) >= 0) j++;
                double d = Double.parseDouble(s.substring(i, j));
                i = j;
                return d;
            }
        }
    }
}
