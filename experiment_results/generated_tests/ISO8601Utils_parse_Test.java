package com.google.gson.internal.bind.util;

import static org.junit.Assert.*;

import java.text.ParseException;
import java.text.ParsePosition;
import java.util.Date;
import java.util.TimeZone;

import org.junit.Before;
import org.junit.Test;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;

public class ISO8601Utils_parse_Test {

    private ParsePosition parsePosition;

    @Before
    public void setUp() {
        parsePosition = new ParsePosition(0);
    }

    @Test
    public void testParseValidDateWithoutTime() throws ParseException {
        String dateStr = "2023-10-15";
        Date result = ISO8601Utils.parse(dateStr, parsePosition);
        assertNotNull(result);
        assertEquals("2023-10-15", ISO8601Utils.format(result, true));
        assertEquals(10, parsePosition.getIndex());
    }

    @Test
    public void testParseValidDateWithTime() throws ParseException {
        String dateStr = "2023-10-15T14:30:45";
        Date result = ISO8601Utils.parse(dateStr, parsePosition);
        assertNotNull(result);
        assertEquals("2023-10-15T14:30:45", ISO8601Utils.format(result, true));
        assertEquals(19, parsePosition.getIndex());
    }

    @Test
    public void testParseValidDateWithMilliseconds() throws ParseException {
        String dateStr = "2023-10-15T14:30:45.123";
        Date result = ISO8601Utils.parse(dateStr, parsePosition);
        assertNotNull(result);
        assertEquals("2023-10-15T14:30:45.123", ISO8601Utils.format(result, true));
        assertEquals(23, parsePosition.getIndex());
    }

    @Test
    public void testParseValidDateWithTimezoneUTC() throws ParseException {
        String dateStr = "2023-10-15T14:30:45Z";
        Date result = ISO8601Utils.parse(dateStr, parsePosition);
        assertNotNull(result);
        assertEquals("2023-10-15T14:30:45Z", ISO8601Utils.format(result, true));
        assertEquals(20, parsePosition.getIndex());
    }

    @Test
    public void testParseValidDateWithTimezoneOffset() throws ParseException {
        String dateStr = "2023-10-15T14:30:45+02:00";
        Date result = ISO8601Utils.parse(dateStr, parsePosition);
        assertNotNull(result);
        assertEquals("2023-10-15T14:30:45+02:00", ISO8601Utils.format(result, true));
        assertEquals(22, parsePosition.getIndex());
    }

    @Test
    public void testParseValidDateWithTimezoneOffsetNoColon() throws ParseException {
        String dateStr = "2023-10-15T14:30:45+0200";
        Date result = ISO8601Utils.parse(dateStr, parsePosition);
        assertNotNull(result);
        assertEquals("2023-10-15T14:30:45+02:00", ISO8601Utils.format(result, true));
        assertEquals(21, parsePosition.getIndex());
    }

    @Test
    public void testParseDateWithZeroMilliseconds() throws ParseException {
        String dateStr = "2023-10-15T14:30:45.000";
        Date result = ISO8601Utils.parse(dateStr, parsePosition);
        assertNotNull(result);
        assertEquals("2023-10-15T14:30:45.000", ISO8601Utils.format(result, true));
        assertEquals(23, parsePosition.getIndex());
    }

    @Test
    public void testParseDateWithOneMillisecondDigit() throws ParseException {
        String dateStr = "2023-10-15T14:30:45.1";
        Date result = ISO8601Utils.parse(dateStr, parsePosition);
        assertNotNull(result);
        assertEquals("2023-10-15T14:30:45.100", ISO8601Utils.format(result, true));
        assertEquals(22, parsePosition.getIndex());
    }

    @Test
    public void testParseDateWithTwoMillisecondDigits() throws ParseException {
        String dateStr = "2023-10-15T14:30:45.12";
        Date result = ISO8601Utils.parse(dateStr, parsePosition);
        assertNotNull(result);
        assertEquals("2023-10-15T14:30:45.120", ISO8601Utils.format(result, true));
        assertEquals(23, parsePosition.getIndex());
    }

    @Test(expected = ParseException.class)
    public void testParseDateInvalidTimezoneIndicator() throws ParseException {
        String dateStr = "2023-10-15T14:30:45X";
        ISO8601Utils.parse(dateStr, parsePosition);
    }

    @Test(expected = ParseException.class)
    public void testParseDateNoTimezoneIndicator() throws ParseException {
        String dateStr = "2023-10-15T14:30:45";
        ISO8601Utils.parse(dateStr, parsePosition);
    }

    @Test(expected = ParseException.class)
    public void testParseDateInvalidFormat() throws ParseException {
        String dateStr = "2023/10/15T14:30:45";
        ISO8601Utils.parse(dateStr, parsePosition);
    }

    @Test(expected = ParseException.class)
    public void testParseDateInvalidYear() throws ParseException {
        String dateStr = "23-10-15T14:30:45Z";
        ISO8601Utils.parse(dateStr, parsePosition);
    }

    @Test(expected = ParseException.class)
    public void testParseDateInvalidMonth() throws ParseException {
        String dateStr = "2023-13-15T14:30:45Z";
        ISO8601Utils.parse(dateStr, parsePosition);
    }

    @Test(expected = ParseException.class)
    public void testParseDateInvalidDay() throws ParseException {
        String dateStr = "2023-10-32T14:30:45Z";
        ISO8601Utils.parse(dateStr, parsePosition);
    }

    @Test(expected = ParseException.class)
    public void testParseDateInvalidHour() throws ParseException {
        String dateStr = "2023-10-15T25:30:45Z";
        ISO8601Utils.parse(dateStr, parsePosition);
    }

    @Test(expected = ParseException.class)
    public void testParseDateInvalidMinute() throws ParseException {
        String dateStr = "2023-10-15T14:60:45Z";
        ISO8601Utils.parse(dateStr, parsePosition);
    }

    @Test(expected = ParseException.class)
    public void testParseDateInvalidSecond() throws ParseException {
        String dateStr = "2023-10-15T14:30:61Z";
        ISO8601Utils.parse(dateStr, parsePosition);
    }

    @Test
    public void testParseDateWithNegativeOffset() throws ParseException {
        String dateStr = "2023-10-15T14:30:45-05:00";
        Date result = ISO8601Utils.parse(dateStr, parsePosition);
        assertNotNull(result);
        assertEquals("2023-10-15T14:30:45-05:00", ISO8601Utils.format(result, true));
        assertEquals(22, parsePosition.getIndex());
    }

    @Test
    public void testParseDateWithZeroOffset() throws ParseException {
        String dateStr = "2023-10-15T14:30:45+00:00";
        Date result = ISO8601Utils.parse(dateStr, parsePosition);
        assertNotNull(result);
        assertEquals("2023-10-15T14:30:45Z", ISO8601Utils.format(result, true));
        assertEquals(22, parsePosition.getIndex());
    }

    @Test
    public void testParseDateWithSingleDigitHour() throws ParseException {
        String dateStr = "2023-10-15T9:30:45.123Z";
        Date result = ISO8601Utils.parse(dateStr, parsePosition);
        assertNotNull(result);
        assertEquals("2023-10-15T09:30:45.123Z", ISO8601Utils.format(result, true));
        assertEquals(24, parsePosition.getIndex());
    }

    @Test
    public void testParseDateWithSingleDigitMinute() throws ParseException {
        String dateStr = "2023-10-15T14:5:45.123Z";
        Date result = ISO8601Utils.parse(dateStr, parsePosition);
        assertNotNull(result);
        assertEquals("2023-10-15T14:05:45.123Z", ISO8601Utils.format(result, true));
        assertEquals(24, parsePosition.getIndex());
    }

    @Test
    public void testParseDateWithSingleDigitSecond() throws ParseException {
        String dateStr = "2023-10-15T14:30:5.123Z";
        Date result = ISO8601Utils.parse(dateStr, parsePosition);
        assertNotNull(result);
        assertEquals("2023-10-15T14:30:05.123Z", ISO8601Utils.format(result, true));
        assertEquals(24, parsePosition.getIndex());
    }

    @Test
    public void testParseDateWithNoTime() throws ParseException {
        String dateStr = "2023-10-15";
        parsePosition = new ParsePosition(0);
        Date result = ISO8601Utils.parse(dateStr, parsePosition);
        assertNotNull(result);
        assertEquals("2023-10-15", ISO8601Utils.format(result, true));
        assertEquals(10, parsePosition.getIndex());
    }
}