package integration;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

import model.Proof;

public final class ProofVerifier {

    private ProofVerifier() {
        // utility
    }

    private record Step(String formula, String rule) {}

    public static void main(String[] args) throws IOException {
        try {
            ProofInputs inputs = readInputs();
            boolean valid = evaluate(inputs);
            System.out.println("{\"valid\":" + valid + "}");
        } catch (Exception ex) {
            String message = ex.getMessage();
            if (message == null) {
                message = ex.getClass().getSimpleName();
            }
            System.out.println("{\"valid\":false,\"error\":\"" + escape(message) + "\"}");
        }
    }

    private static ProofInputs readInputs() throws IOException {
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8))) {
            String line;
            String conclusion = null;
            List<String> premises = new ArrayList<>();
            List<Step> steps = new ArrayList<>();
            while ((line = reader.readLine()) != null) {
                line = line.trim();
                if (line.isEmpty() || line.startsWith("#")) {
                    continue;
                }
                if (line.startsWith("CONCLUSION|")) {
                    conclusion = line.substring("CONCLUSION|".length());
                } else if (line.startsWith("PREMISE|")) {
                    premises.add(line.substring("PREMISE|".length()));
                } else if (line.startsWith("STEP|")) {
                    String payload = line.substring("STEP|".length());
                    String formula;
                    String rule;
                    int splitIndex = payload.indexOf("||");
                    if (splitIndex >= 0) {
                        formula = payload.substring(0, splitIndex);
                        rule = payload.substring(splitIndex + 2);
                    } else {
                        formula = payload;
                        rule = "";
                    }
                    steps.add(new Step(formula, rule));
                } else {
                    throw new IllegalArgumentException("Unrecognized line: " + line);
                }
            }
            if (conclusion == null) {
                throw new IllegalArgumentException("Missing conclusion");
            }
            return new ProofInputs(conclusion, List.copyOf(premises), List.copyOf(steps));
        }
    }

    // pass inputs line by line into proof

    private static boolean evaluate(ProofInputs inputs) {
        Proof proof = new Proof();
        PrintStream originalOut = System.out;
        ByteArrayOutputStream sink = new ByteArrayOutputStream();
        boolean result;
        try (PrintStream silent = new PrintStream(sink)) {
            System.setOut(silent);
            int rowIndex = 0;
            for (String premise : inputs.premises()) {
                proof.addRow();
                // updateRow is a stub in Proof; call the concrete APIs directly (1-based row numbers)
                proof.updateFormulaRow(premise, rowIndex + 1);
                try {
                    proof.updateRuleRow("Premise", rowIndex + 1);
                } catch (IllegalAccessException | InstantiationException e) {
                    throw new IllegalStateException(e);
                }
                rowIndex++;
            }
            for (Step step : inputs.steps()) {
                proof.addRow();
                // Fill formula then rule to mirror UI behaviour
                proof.updateFormulaRow(step.formula(), rowIndex + 1);
                try {
                    proof.updateRuleRow(step.rule(), rowIndex + 1);
                } catch (IllegalAccessException | InstantiationException e) {
                    throw new IllegalStateException(e);
                }
                rowIndex++;
            }
            proof.updateConclusion(inputs.conclusion());
            result = proof.verifyProof();
        } finally {
            System.setOut(originalOut);
        }

        if (result) {
            result = concludes(inputs);
        }
        return result;
    }

    private static boolean concludes(ProofInputs inputs) {
        List<Step> steps = inputs.steps();
        if (steps.isEmpty()) {
            return false;
        }
        Step last = steps.get(steps.size() - 1);
        return normalize(last.formula()).equals(normalize(inputs.conclusion()));
    }

    private static String normalize(String formula) {
        return formula == null ? "" : formula.strip();
    }

    private static String escape(String message) {
        StringBuilder sb = new StringBuilder(message.length() + 8);
        for (char ch : message.toCharArray()) {
            if (ch == '"' || ch == '\\') {
                sb.append('\\').append(ch);
            } else if (ch >= 0x20 && ch <= 0x7E) {
                sb.append(ch);
            } else {
                sb.append("\\u").append(String.format("%04x", (int) ch));
            }
        }
        return sb.toString();
    }

    private record ProofInputs(String conclusion, List<String> premises, List<Step> steps) {}
}
