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
        List<Candidate> candidates = new ArrayList<>();
        for (Constructor<?> k : c.getConstructors()) {
            Candidate candidate = candidate(k, args);
            if (candidate != null) candidates.add(candidate);
        }
        if (!candidates.isEmpty()) {
            Candidate selected = select(candidates, c.getName(), "<init>",
                true, args.length);
            try {
                return wrap(((Constructor<?>) selected.member).newInstance(
                    selected.converted));
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
        Map<String, Method> unique = new LinkedHashMap<>();
        for (Method m : c.getMethods()) {
            if (!m.getName().equals(method)) continue;
            if (Modifier.isStatic(m.getModifiers()) != wantStatic || m.isBridge())
                continue;
            String key = methodKey(m);
            Method previous = unique.get(key);
            if (previous == null || moreSpecificDeclaration(
                    c, m.getDeclaringClass(), previous.getDeclaringClass()))
                unique.put(key, m);
        }
        List<Candidate> candidates = new ArrayList<>();
        for (Method m : unique.values()) {
            Candidate candidate = candidate(m, args);
            if (candidate != null) candidates.add(candidate);
        }
        if (!candidates.isEmpty()) {
            Candidate selected = select(candidates, c.getName(), method,
                false, args.length);
            try {
                return wrap(((Method) selected.member).invoke(
                    target, selected.converted));
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
            Object converted = coerceOne(v, f.getType());
            if (converted == CONV_FAIL)
                throw new RuntimeException("bad type for field " + field);
            f.set(null, converted);
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

    // ---------------- overload selection and conversion ----------------
    static final Object CONV_FAIL = new Object();

    static final class Conversion {
        final Object value;
        final int level;
        Conversion(Object value, int level) {
            this.value = value;
            this.level = level;
        }
    }

    static final class Candidate {
        final Executable member;
        final Object[] converted;
        final int[] levels;
        final Class<?>[] argumentTypes;
        final boolean expanded;
        Candidate(Executable member, Object[] converted, int[] levels,
                  Class<?>[] argumentTypes, boolean expanded) {
            this.member = member;
            this.converted = converted;
            this.levels = levels;
            this.argumentTypes = argumentTypes;
            this.expanded = expanded;
        }
    }

    static Candidate candidate(Executable member, Object[] args) {
        Class<?>[] params = member.getParameterTypes();
        boolean expanded = member.isVarArgs();
        int fixed = expanded ? params.length - 1 : params.length;
        if ((!expanded && args.length != params.length) ||
                (expanded && args.length < fixed)) return null;
        Object[] out = new Object[params.length];
        int[] levels = new int[args.length];
        Class<?>[] argTypes = new Class<?>[args.length];
        for (int i = 0; i < fixed; i++) {
            Conversion cv = convert(args[i], params[i]);
            if (cv == null) return null;
            out[i] = cv.value;
            levels[i] = cv.level;
            argTypes[i] = params[i];
        }
        if (expanded) {
            Class<?> component = params[params.length - 1].getComponentType();
            Object array = Array.newInstance(component, args.length - fixed);
            for (int i = fixed; i < args.length; i++) {
                Conversion cv = convert(args[i], component);
                if (cv == null) return null;
                Array.set(array, i - fixed, cv.value);
                levels[i] = cv.level;
                argTypes[i] = component;
            }
            out[params.length - 1] = array;
        }
        return new Candidate(member, out, levels, argTypes, expanded);
    }

    static Candidate select(List<Candidate> candidates, String targetClass,
                            String method, boolean constructor, int argCount) {
        List<Candidate> undominated = new ArrayList<>();
        for (Candidate candidate : candidates) {
            boolean dominated = false;
            for (Candidate other : candidates) {
                if (other != candidate && better(other, candidate)) {
                    dominated = true;
                    break;
                }
            }
            if (!dominated) undominated.add(candidate);
        }
        if (undominated.size() == 1) return undominated.get(0);
        throw new RuntimeException(ambiguityMessage(undominated, targetClass,
            method, constructor, argCount));
    }

    /** True when a is strictly preferred to b under the SPEC partial order. */
    static boolean better(Candidate a, Candidate b) {
        boolean strict = false;
        boolean identical = true;
        for (int i = 0; i < a.levels.length; i++) {
            if (a.levels[i] < b.levels[i]) {
                strict = true;
                identical = false;
                continue;
            }
            if (a.levels[i] > b.levels[i]) return false;
            Class<?> at = a.argumentTypes[i], bt = b.argumentTypes[i];
            if (at == bt) continue;
            identical = false;
            if (at.isPrimitive() || bt.isPrimitive()) return false;
            if (bt.isAssignableFrom(at)) {
                strict = true;
            } else {
                // Equal-rank unrelated references are incomparable.
                return false;
            }
        }
        if (strict) return true;
        return identical && !a.expanded && b.expanded;
    }

    static String ambiguityMessage(List<Candidate> candidates, String targetClass,
                                   String method, boolean constructor,
                                   int argCount) {
        List<String> signatures = new ArrayList<>();
        for (Candidate candidate : candidates)
            signatures.add(signature(candidate.member));
        signatures.sort(JReflect::compareCodePoints);
        if (constructor) {
            return "java: ambiguous constructor " + targetClass + "(" +
                argCount + " args): [" +
                String.join(", ", signatures) + "]";
        }
        return "java: ambiguous method " + targetClass + "." + method + "(" +
            argCount + " args): [" + String.join(", ", signatures) + "]";
    }

    static String signature(Executable member) {
        String owner = member.getDeclaringClass().getName();
        String name = member instanceof Constructor<?> ? "<init>" :
            ((Method) member).getName();
        Class<?>[] params = member.getParameterTypes();
        List<String> names = new ArrayList<>();
        for (int i = 0; i < params.length; i++) {
            Class<?> type = params[i];
            if (member.isVarArgs() && i == params.length - 1)
                names.add(typeName(type.getComponentType()) + "...");
            else
                names.add(typeName(type));
        }
        return owner + "#" + name + "(" + String.join(",", names) + ")";
    }

    static String typeName(Class<?> type) {
        return type.isArray() ? typeName(type.getComponentType()) + "[]" :
            type.getName();
    }

    static int compareCodePoints(String a, String b) {
        int ai = 0, bi = 0;
        while (ai < a.length() && bi < b.length()) {
            int ac = a.codePointAt(ai), bc = b.codePointAt(bi);
            if (ac != bc) return Integer.compare(ac, bc);
            ai += Character.charCount(ac);
            bi += Character.charCount(bc);
        }
        return Integer.compare(a.length() - ai, b.length() - bi);
    }

    static String methodKey(Method m) {
        StringBuilder key = new StringBuilder(m.getName()).append('(');
        for (Class<?> type : m.getParameterTypes())
            key.append(type.getName()).append(';');
        return key.append(')').toString();
    }

    static boolean moreSpecificDeclaration(Class<?> target, Class<?> a,
                                           Class<?> b) {
        if (b.isAssignableFrom(a) && a != b) return true;
        if (a.isAssignableFrom(b) && a != b) return false;
        int da = declarationDistance(target, a), db = declarationDistance(target, b);
        if (da != db) return da < db;
        return compareCodePoints(a.getName(), b.getName()) < 0;
    }

    static int declarationDistance(Class<?> target, Class<?> ancestor) {
        if (target == ancestor) return 0;
        Queue<Class<?>> queue = new ArrayDeque<>();
        Map<Class<?>, Integer> distances = new HashMap<>();
        queue.add(target);
        distances.put(target, 0);
        while (!queue.isEmpty()) {
            Class<?> current = queue.remove();
            int distance = distances.get(current);
            Class<?> parent = current.getSuperclass();
            if (parent != null && !distances.containsKey(parent)) {
                if (parent == ancestor) return distance + 1;
                distances.put(parent, distance + 1);
                queue.add(parent);
            }
            for (Class<?> iface : current.getInterfaces()) {
                if (distances.containsKey(iface)) continue;
                if (iface == ancestor) return distance + 1;
                distances.put(iface, distance + 1);
                queue.add(iface);
            }
        }
        return Integer.MAX_VALUE;
    }

    static Object coerceOne(Object value, Class<?> target) {
        Conversion cv = convert(value, target);
        return cv == null ? CONV_FAIL : cv.value;
    }

    static Conversion convert(Object value, Class<?> target) {
        if (value instanceof JClass) return null;
        if (value instanceof JObj) value = ((JObj) value).o;
        if (value == null)
            return target.isPrimitive() ? null : new Conversion(null, 1);

        if (value instanceof Double) {
            double d = (Double) value;
            if (target == double.class || target == Double.class)
                return new Conversion(d, 0);
            if (target == float.class || target == Float.class) {
                float f = (float) d;
                boolean same = Double.isNaN(d) ? Float.isNaN(f) :
                    Double.doubleToRawLongBits((double) f) ==
                    Double.doubleToRawLongBits(d);
                return new Conversion(f, same ? 1 : 2);
            }
            if (target == long.class || target == Long.class) {
                long n = (long) d;
                return new Conversion(n, integralExact(d, -0x1.0p63,
                    Math.nextDown(0x1.0p63), (double) n) ? 1 : 2);
            }
            if (target == int.class || target == Integer.class) {
                int n = (int) d;
                return new Conversion(n, integralExact(d, Integer.MIN_VALUE,
                    Integer.MAX_VALUE, (double) n) ? 1 : 2);
            }
            if (target == short.class || target == Short.class) {
                short n = (short) d;
                return new Conversion(n, integralExact(d, Short.MIN_VALUE,
                    Short.MAX_VALUE, (double) n) ? 1 : 2);
            }
            if (target == byte.class || target == Byte.class) {
                byte n = (byte) d;
                return new Conversion(n, integralExact(d, Byte.MIN_VALUE,
                    Byte.MAX_VALUE, (double) n) ? 1 : 2);
            }
            if (!target.isPrimitive() && target.isInstance(value))
                return new Conversion(value, target == Double.class ? 0 : 1);
            return null;
        }
        if (value instanceof String) {
            if (target == String.class) return new Conversion(value, 0);
            if ((target == char.class || target == Character.class) &&
                    ((String) value).length() == 1)
                return new Conversion(((String) value).charAt(0), 1);
            if (!target.isPrimitive() && target.isInstance(value))
                return new Conversion(value, target == value.getClass() ? 0 : 1);
            return null;
        }
        if (value instanceof Boolean) {
            if (target == boolean.class || target == Boolean.class)
                return new Conversion(value, 0);
            if (!target.isPrimitive() && target.isInstance(value))
                return new Conversion(value, target == Boolean.class ? 0 : 1);
            return null;
        }
        if (value instanceof List) {
            if (!target.isPrimitive() && target.isInstance(value)) {
                int level = (target == List.class || target == value.getClass())
                    ? 0 : 1;
                return new Conversion(value, level);
            }
            if (target.isArray()) {
                List<?> list = (List<?>) value;
                Class<?> component = target.getComponentType();
                Object array = Array.newInstance(component, list.size());
                for (int i = 0; i < list.size(); i++) {
                    Conversion cv = convert(list.get(i), component);
                    if (cv == null) return null;
                    Array.set(array, i, cv.value);
                }
                return new Conversion(array, 2);
            }
            return null;
        }
        if (value instanceof Map) {
            if (!target.isPrimitive() && target.isInstance(value)) {
                int level = (target == Map.class || target == value.getClass())
                    ? 0 : 1;
                return new Conversion(value, level);
            }
            return null;
        }
        if (!target.isPrimitive() && target.isInstance(value))
            return new Conversion(value, target == value.getClass() ? 0 : 1);
        return null;
    }

    static boolean integralExact(double original, double min, double max,
                                 double converted) {
        return Double.isFinite(original) && original == Math.rint(original) &&
            original >= min && original <= max &&
            !(original == 0.0 &&
              Double.doubleToRawLongBits(original) < 0) &&
            converted == original;
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
        // BigDecimal/BigInteger exist precisely to NOT be doubles;
        // keep them as opaque objects so their methods keep working.
        if (v instanceof java.math.BigDecimal || v instanceof java.math.BigInteger)
            return new JObj(v);
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
