import java.util.*;

/**
 * LtJavaDaemon: serves Java interop to the Python backend.
 *
 * Started lazily by generated Python code (only when `java` handles are
 * actually used). Speaks the same JSON line protocol as ltpy.py:
 * one request per line on stdin, one response per line on stdout.
 *
 * Ops: new / call / get / set / repr / truthy / quit.
 * Targets: {"class": "com.foo.Bar"} (static) or {"id": N} (instance).
 * Java objects that are not JSON natives come back as {"__jref": N}
 * and live in the server-side table until quit.
 */
public class LtJavaDaemon {

    static Map<Long, JReflect.JObj> objs = new HashMap<>();
    static long nextId = 1;

    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in, "UTF-8");
        while (sc.hasNextLine()) {
            String line = sc.nextLine().trim();
            if (line.isEmpty()) continue;
            try {
                Object req = LtRt.Json.parse(line);
                Map<?, ?> m = (Map<?, ?>) req;
                String op = (String) m.get("op");
                if (op.equals("quit")) {
                    System.out.println("{\"value\": null}");
                    System.out.flush();
                    break;
                }
                Object result;
                switch (op) {
                    case "new": {
                        String cls = (String) m.get("class");
                        Object[] a = decodeArgs((List<?>) m.get("args"));
                        result = JReflect.construct(new JReflect.JClass(cls), a);
                        break;
                    }
                    case "call": {
                        Object target = decodeTarget(m.get("target"));
                        String method = (String) m.get("method");
                        Object[] a = decodeArgs((List<?>) m.get("args"));
                        if (target instanceof JReflect.JClass)
                            result = JReflect.callStatic((JReflect.JClass) target, method, a);
                        else
                            result = JReflect.call((JReflect.JObj) target, method, a);
                        break;
                    }
                    case "get": {
                        Object target = decodeTarget(m.get("target"));
                        String field = (String) m.get("field");
                        if (target instanceof JReflect.JClass)
                            result = JReflect.getField((JReflect.JClass) target, field);
                        else
                            result = JReflect.getField((JReflect.JObj) target, field);
                        break;
                    }
                    case "set": {
                        Object target = decodeTarget(m.get("target"));
                        String field = (String) m.get("field");
                        Object v = decode(m.get("value"));
                        if (target instanceof JReflect.JClass)
                            result = JReflect.setField((JReflect.JClass) target, field, v);
                        else
                            result = JReflect.setField((JReflect.JObj) target, field, v);
                        break;
                    }
                    case "repr":
                        result = JReflect.repr(decodeTarget(m.get("target")));
                        break;
                    case "tolist": {
                        Object target = decodeTarget(m.get("target"));
                        Object o = (target instanceof JReflect.JObj)
                            ? ((JReflect.JObj) target).o : null;
                        if (o instanceof List || (o != null && o.getClass().isArray()))
                            result = JReflect.deepWrap(o);
                        else
                            throw new RuntimeException("cannot iterate " +
                                (o == null ? "null" : o.getClass().getName()));
                        break;
                    }
                    case "getitem": {
                        Object target = decodeTarget(m.get("target"));
                        Object k = decode(m.get("key"));
                        Object o = (target instanceof JReflect.JObj)
                            ? ((JReflect.JObj) target).o : null;
                        if (o instanceof List) {
                            List<?> l = (List<?>) o;
                            result = JReflect.wrap(l.get(toIndex(k, l.size())));
                        } else if (o instanceof Map) {
                            Map<?, ?> mp = (Map<?, ?>) o;
                            if (!mp.containsKey(k))
                                throw new RuntimeException("key not found: " + k);
                            result = JReflect.wrap(mp.get(k));
                        } else {
                            throw new RuntimeException("cannot index " +
                                (o == null ? "null" : o.getClass().getName()));
                        }
                        break;
                    }
                    case "setitem": {
                        Object target = decodeTarget(m.get("target"));
                        Object k = decode(m.get("key"));
                        Object v = decode(m.get("value"));
                        Object o = (target instanceof JReflect.JObj)
                            ? ((JReflect.JObj) target).o : null;
                        if (o instanceof List) {
                            @SuppressWarnings("unchecked")
                            List<Object> l = (List<Object>) o;
                            l.set(toIndex(k, l.size()), v);
                        } else if (o instanceof Map) {
                            @SuppressWarnings("unchecked")
                            Map<Object, Object> mp = (Map<Object, Object>) o;
                            mp.put(k, v);
                        } else {
                            throw new RuntimeException("cannot index-assign " +
                                (o == null ? "null" : o.getClass().getName()));
                        }
                        result = null;
                        break;
                    }
                    case "truthy":
                        result = Boolean.TRUE; // any live handle is truthy
                        break;
                    default:
                        throw new RuntimeException("unknown op: " + op);
                }
                System.out.println(LtRt.Json.encode(
                    Collections.singletonMap("value", encode(result))));
            } catch (Exception e) {
                System.out.println("{\"error\": " +
                    LtRt.Json.encode(JReflect.rootCause(e)) + "}");
            }
            System.out.flush();
        }
    }

    static Object decodeTarget(Object t) {
        Map<?, ?> m = (Map<?, ?>) t;
        if (m.containsKey("class"))
            return new JReflect.JClass((String) m.get("class"));
        Number id = (Number) m.get("id");
        JReflect.JObj o = objs.get(id.longValue());
        if (o == null) throw new RuntimeException("stale java ref: " + id);
        return o;
    }

    /** Shared integer-index rule: integral numbers only, negatives from the end. */
    static int toIndex(Object k, int n) {
        if (!(k instanceof Number))
            throw new RuntimeException("index must be a number");
        double d = ((Number) k).doubleValue();
        if (d != Math.rint(d))
            throw new RuntimeException("index must be an integer");
        int i = (int) d;
        if (i < 0) i += n;
        if (i < 0 || i >= n)
            throw new RuntimeException("index out of range: " + (int) d);
        return i;
    }

    static Object[] decodeArgs(List<?> l) {
        if (l == null) return new Object[0];
        Object[] out = new Object[l.size()];
        for (int i = 0; i < l.size(); i++) out[i] = decode(l.get(i));
        return out;
    }

    /** JSON value -> Latent value (JObj refs stay live). */
    static Object decode(Object v) {
        if (v instanceof Map) {
            Map<?, ?> m = (Map<?, ?>) v;
            if (m.size() == 1 && m.containsKey("__num")) {
                String number = String.valueOf(m.get("__num"));
                if (number.equals("nan")) return Double.NaN;
                if (number.equals("inf")) return Double.POSITIVE_INFINITY;
                if (number.equals("-inf")) return Double.NEGATIVE_INFINITY;
            }
            if (m.size() == 1 && m.containsKey("__map")) {
                Object rawEntries = m.get("__map");
                if (!(rawEntries instanceof List))
                    throw new RuntimeException("invalid Java map envelope");
                Map<String, Object> result = new LinkedHashMap<>();
                for (Object rawPair : (List<?>) rawEntries) {
                    if (!(rawPair instanceof List) || ((List<?>) rawPair).size() != 2)
                        throw new RuntimeException("invalid Java map entry");
                    List<?> pair = (List<?>) rawPair;
                    result.put(String.valueOf(pair.get(0)), decode(pair.get(1)));
                }
                return result;
            }
            if (m.size() == 1 && m.containsKey("__jref")) {
                // handle arriving as an argument (request envelopes use
                // the "id" spelling via decodeTarget instead)
                Number id = (Number) m.get("__jref");
                JReflect.JObj o = objs.get(id.longValue());
                if (o == null)
                    throw new RuntimeException("stale java ref: " + id);
                return o;
            }
            if (m.size() == 1 && m.containsKey("__jclass"))
                return new JReflect.JClass((String) m.get("__jclass"));
            Map<String, Object> r = new LinkedHashMap<>();
            for (Map.Entry<?, ?> e : m.entrySet())
                r.put(String.valueOf(e.getKey()), decode(e.getValue()));
            return r;
        }
        if (v instanceof List) {
            List<Object> r = new ArrayList<>();
            for (Object x : (List<?>) v) r.add(decode(x));
            return r;
        }
        return v;
    }

    /** Latent value -> JSON value (live Java objects become __jref). */
    static Object encode(Object v) {
        if (v instanceof JReflect.JObj) {
            long id = nextId++;
            objs.put(id, (JReflect.JObj) v);
            return Collections.singletonMap("__jref", (double) id);
        }
        if (v instanceof JReflect.JClass)
            return Collections.singletonMap("__jclass",
                ((JReflect.JClass) v).name);
        if (v instanceof List) {
            List<Object> r = new ArrayList<>();
            for (Object x : (List<?>) v) r.add(encode(x));
            return r;
        }
        if (v instanceof Map) {
            List<Object> entries = new ArrayList<>();
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                List<Object> pair = new ArrayList<>(2);
                pair.add(String.valueOf(e.getKey()));
                pair.add(encode(e.getValue()));
                entries.add(pair);
            }
            return Collections.singletonMap("__map", entries);
        }
        return v; // Double / String / Boolean / null
    }
}
