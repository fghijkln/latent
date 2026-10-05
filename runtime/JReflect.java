import java.util.*;
import java.io.*;
import java.lang.reflect.*;

/**
 * JReflect: shared Java-interop core for Latent.
 *
 * Used directly by LtRt (Java backend) and by LtJavaDaemon
 * (Python backend, over a JSON line protocol).
 *
 * Latent value model: Double | String | Boolean | null |
 *   ArrayList<Object> | LinkedHashMap<String,Object> | JClass | JObj
 */
public class JReflect {

    /** Lazy Java class handle: Class.forName happens on first real use. */
    public static class JClass {
        public final String name;
        private Class<?> c;

        public JClass(String name) { this.name = name; }

        public Class<?> resolve() {
            if (c == null) {
                try {
                    c = Class.forName(name);
                } catch (ClassNotFoundException e) {
                    throw new RuntimeException("java: class not found: " + name +
                        " (nested classes need Outer$Inner syntax)");
                }
            }
            return c;
        }
    }

    /** Opaque handle to a live Java object. */
    public static class JObj {
        public final Object o;
        public JObj(Object o) { this.o = o; }
    }

    // ---------------- entry points ----------------
    public static Object construct(JClass jc, Object[] args) {
        Class<?> c = jc.resolve();
        for (Constructor<?> k : c.getConstructors()) {
            Object[] conv = coerce(args, k.getParameterTypes(), k.isVarArgs());
            if (conv == null) continue;
            try {
                return wrap(k.newInstance(conv));
            } catch (Exception e) {
                throw new RuntimeException("java: new " + jc.name +
                    " failed: " + rootCause(e));
            }
        }
        throw new RuntimeException("java: no matching constructor " +
            jc.name + "(" + args.length + " args)");
    }

    public static Object callStatic(JClass jc, String method, Object[] args) {
        return callOn(jc.resolve(), null, method, args, true);
    }

    public static Object call(JObj jo, String method, Object[] args) {
        return callOn(jo.o.getClass(), jo.o, method, args, false);
    }

    static Object callOn(Class<?> c, Object target, String method,
                         Object[] args, boolean wantStatic) {
        for (Method m : c.getMethods()) {
            if (!m.getName().equals(method)) continue;
            if (wantStatic && !Modifier.isStatic(m.getModifiers())) continue;
            Object[] conv = coerce(args, m.getParameterTypes(), m.isVarArgs());
            if (conv == null) continue;
            try {
                return wrap(m.invoke(target, conv));
            } catch (Exception e) {
                throw new RuntimeException("java: " + c.getName() + "." +
                    method + " failed: " + rootCause(e));
            }
        }
        throw new RuntimeException("java: no matching method " +
            c.getName() + "." + method + "(" + args.length + " args)");
    }

    public static Object getField(JClass jc, String field) {
        try {
            return wrap(jc.resolve().getField(field).get(null));
        } catch (Exception e) {
            throw new RuntimeException("java: no static field " +
                jc.name + "." + field);
        }
    }

    public static Object getField(JObj jo, String field) {
        try {
            return wrap(jo.o.getClass().getField(field).get(jo.o));
        } catch (Exception e) {
            throw new RuntimeException("java: no field " + field +
                " on " + jo.o.getClass().getName());
        }
    }

    public static Object setField(JClass jc, String field, Object v) {
        try {
            Field f = jc.resolve().getField(field);
            f.set(null, coerceOne(v, f.getType()));
            return null;
        } catch (RuntimeException e) {
            throw e;
        } catch (Exception e) {
            throw new RuntimeException("java: cannot set static field " +
                jc.name + "." + field + ": " + rootCause(e));
        }
    }

    public static Object setField(JObj jo, String field, Object v) {
        try {
            Field f = jo.o.getClass().getField(field);
            Object cv = coerceOne(v, f.getType());
            if (cv == CONV_FAIL)
                throw new RuntimeException("bad type for field " + field);
            f.set(jo.o, cv);
            return null;
        } catch (RuntimeException e) {
            throw e;
        } catch (Exception e) {
            throw new RuntimeException("java: cannot set field " + field +
                ": " + rootCause(e));
        }
    }

    // ---------------- overload coercion ----------------
    static final Object CONV_FAIL = new Object();

    static Object[] coerce(Object[] args, Class<?>[] params, boolean varArgs) {
        int fixed = varArgs ? params.length - 1 : params.length;
        if (!varArgs && args.length != params.length) return null;
        if (varArgs && args.length < fixed) return null;
        Object[] out = new Object[params.length];
        for (int i = 0; i < fixed; i++) {
            Object cv = coerceOne(args[i], params[i]);
            if (cv == CONV_FAIL) return null;
            out[i] = cv;
        }
        if (varArgs) {
            Class<?> comp = params[params.length - 1].getComponentType();
            Object arr = Array.newInstance(comp, args.length - fixed);
            for (int i = fixed; i < args.length; i++) {
                Object cv = coerceOne(args[i], comp);
                if (cv == CONV_FAIL) return null;
                Array.set(arr, i - fixed, cv);
            }
            out[params.length - 1] = arr;
        }
        return out;
    }

    static Object coerceOne(Object v, Class<?> t) {
        if (v == null) return t.isPrimitive() ? CONV_FAIL : null;
        if (t == Object.class) return v;
        if (v instanceof JObj) {
            Object o = ((JObj) v).o;
            return t.isInstance(o) ? o : CONV_FAIL;
        }
        if (v instanceof JClass) return CONV_FAIL;
        if (t == String.class) return (v instanceof String) ? v : CONV_FAIL;
        if (t.isAssignableFrom(String.class) && v instanceof String) return v;
        if (t == boolean.class || t == Boolean.class)
            return (v instanceof Boolean) ? v : CONV_FAIL;
        if (t == char.class || t == Character.class) {
            if (v instanceof String && ((String) v).length() == 1)
                return ((String) v).charAt(0);
            return CONV_FAIL;
        }
        if (t.isPrimitive() || Number.class.isAssignableFrom(t)) {
            if (!(v instanceof Double)) return CONV_FAIL;
            double d = (Double) v;
            if (t == double.class || t == Double.class) return d;
            if (t == float.class || t == Float.class) return (float) d;
            long l = (long) d; // truncate, same as int()
            if (t == long.class || t == Long.class) return l;
            if (t == int.class || t == Integer.class) return (int) l;
            if (t == short.class || t == Short.class) return (short) l;
            if (t == byte.class || t == Byte.class) return (byte) l;
            return CONV_FAIL;
        }
        if (v instanceof List && t.isAssignableFrom(List.class)) return v;
        if (v instanceof Map && t.isAssignableFrom(Map.class)) return v;
        if (t.isArray() && v instanceof List) {
            List<?> l = (List<?>) v;
            Object arr = Array.newInstance(t.getComponentType(), l.size());
            for (int i = 0; i < l.size(); i++) {
                Object cv = coerceOne(l.get(i), t.getComponentType());
                if (cv == CONV_FAIL) return CONV_FAIL;
                Array.set(arr, i, cv);
            }
            return arr;
        }
        return CONV_FAIL;
    }

    // ---------------- Java -> Latent ----------------
    /**
     * Boundary crossing for call/new/field results: only JSON-native
     * scalars become Latent values. Every other Java object (List, Map,
     * arrays, arbitrary objects) stays an opaque JObj handle, so methods
     * like a.add(...) keep working on it. say/for materialize on demand
     * via deepWrap.
     */
    public static Object wrap(Object v) {
        if (v == null) return null;
        if (v instanceof Double || v instanceof String || v instanceof Boolean)
            return v;
        if (v instanceof Number) return ((Number) v).doubleValue();
        if (v instanceof Character) return String.valueOf((char) (Character) v);
        if (v instanceof JObj || v instanceof JClass) return v;
        return new JObj(v);
    }

    /** Deep conversion for say/for: List/Map/array -> Latent values,
        opaque leaves stay JObj. */
    static Object deepWrap(Object v) {
        if (v == null) return null;
        if (v instanceof Double || v instanceof String || v instanceof Boolean)
            return v;
        if (v instanceof Number) return ((Number) v).doubleValue();
        if (v instanceof Character) return String.valueOf((char) (Character) v);
        if (v instanceof JObj || v instanceof JClass) return v;
        if (v instanceof List) {
            List<Object> r = new ArrayList<>();
            for (Object x : (List<?>) v) r.add(deepWrap(x));
            return r;
        }
        if (v instanceof Map) {
            LinkedHashMap<String, Object> r = new LinkedHashMap<>();
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet())
                r.put(String.valueOf(e.getKey()), deepWrap(e.getValue()));
            return r;
        }
        if (v.getClass().isArray()) {
            int n = Array.getLength(v);
            List<Object> r = new ArrayList<>(n);
            for (int i = 0; i < n; i++) r.add(deepWrap(Array.get(v, i)));
            return r;
        }
        return new JObj(v);
    }

    // ---------------- repr / truthy (Latent semantics) ----------------
    static String numStr(double d) {
        if (Double.isNaN(d)) return "nan";
        if (Double.isInfinite(d)) return d > 0 ? "inf" : "-inf";
        if (d == Math.rint(d) && Math.abs(d) < 1e16) return Long.toString((long) d);
        String s = Double.toString(d);
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

    static String reprValue(Object v) {
        if (v == null) return "nil";
        if (v instanceof Boolean) return (Boolean) v ? "true" : "false";
        if (v instanceof Double) return numStr((Double) v);
        if (v instanceof String) return (String) v;
        if (v instanceof JClass || v instanceof JObj) return repr(v);
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
                sb.append(reprQ(e.getKey())).append(": ")
                  .append(reprQ(e.getValue()));
                first = false;
            }
            return sb.append("}").toString();
        }
        return String.valueOf(v);
    }

    static String reprQ(Object v) {
        if (v instanceof String) return "\"" + escape((String) v) + "\"";
        return reprValue(v);
    }

    /** Latent repr of a java handle. */
    public static String repr(Object h) {
        if (h instanceof JClass) return "<class " + ((JClass) h).name + ">";
        Object o = ((JObj) h).o;
        if (o instanceof List || o instanceof Map || o.getClass().isArray())
            return reprValue(deepWrap(o));
        try {
            return String.valueOf(o);
        } catch (Exception e) {
            return "<java object>";
        }
    }

    static String rootCause(Exception e) {
        Throwable t = e;
        if (t instanceof InvocationTargetException && t.getCause() != null)
            t = t.getCause();
        StringWriter sw = new StringWriter();
        t.printStackTrace(new PrintWriter(sw));
        return sw.toString();
    }
}
