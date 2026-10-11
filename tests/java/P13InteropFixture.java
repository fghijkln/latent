import java.util.List;

public class P13InteropFixture {
    public String collision = "field";
    private int calls = 0;

    public String append(String value) {
        calls++;
        return "one:" + value + ":" + calls;
    }

    public String append(int index, String value) {
        calls++;
        return "two:" + index + ":" + value + ":" + calls;
    }

    public String collision() {
        return "method";
    }

    public String acceptList(List<?> values) {
        return "accepted:" + values.size();
    }

    public static String staticMethod(String value) {
        return value;
    }
}
